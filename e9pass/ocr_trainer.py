"""Дообучение моделей распознавания на размеченных картах (страница «Обучение OCR»).

Все работает на этом компьютере. Инструменты обучения (PaddlePaddle + PaddleOCR, ~2 ГБ) один раз
скачиваются в папку training/toolkit; дальше интернет не нужен. Порядок:
  1) из проверенных карт вырезаются строки (training.build_dataset);
  2) готовая модель дообучается на них: основа модели заморожена, учатся верхние слои (CTC) —
     так быстро даже на обычном процессоре и без риска «сломать» модель на малом наборе;
  3) новая модель проверяется на отложенных картах (каждая пятая) против текущей;
  4) подключается, только если стала точнее; прежнюю можно вернуть.
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

from . import training

Progress = Callable[[str], None]

UV_VERSION = "0.9.5"
PADDLE_PKGS = ["paddlepaddle==3.3.1", "paddle2onnx==2.1.0", "onnx"]
PADDLEOCR_COMMIT = "dab3fe35379033fdcb2d0e9572fac0b36c9a9ebf"
PRETRAINED = "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_pretrained_model/"

# модель: (конфиг PaddleOCR, исходные веса, словарь, ширина строки при обучении, скорость шага)
MODELS = {
    training.LATIN: {
        "title": "Латиница (ФИО, номер, страна)",
        "config": "configs/rec/PP-OCRv6/PP-OCRv6_small_rec.yml",
        "weights": "PP-OCRv6_small_rec_pretrained.pdparams",
        "dict": "ppocr/utils/dict/ppocrv6_dict.txt",
        "lr": 0.0002,
    },
    training.KOREAN: {
        "title": "Корейский (адрес)",
        "config": "configs/rec/PP-OCRv5/multi_language/korean_PP-OCRv5_mobile_rec.yml",
        "weights": "korean_PP-OCRv5_mobile_rec_pretrained.pdparams",
        "dict": "ppocr/utils/dict/ppocrv5_korean_dict.txt",
        "lr": 0.0002,
    },
}
MIN_LINES = 30          # меньше — обучать бессмысленно
PAD_RATIO = 10          # строки дополняются фоном до пропорции 10:1 (ширина 480 при высоте 48)

# Обертка запуска обучения внутри окружения PaddlePaddle: все ядра процессора, только CTC-ветка,
# замороженная основа модели (включая статистику BatchNorm).
RUNNER = r'''
import os, runpy, sys
from paddle.base import core
core.set_num_threads(os.cpu_count() or 4)
script = sys.argv[1]
sys.argv = sys.argv[1:]
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(script))))
import ppocr.modeling.architectures as arch
_orig = arch.build_model
def build_model(config):
    model = _orig(config)
    for p in model.backbone.parameters():
        p.stop_gradient = True
    orig_train = model.train
    def train():
        orig_train()
        model.backbone.eval()
    model.train = train
    model.backbone.eval()
    return model
arch.build_model = build_model
from ppocr.modeling.heads import rec_multi_head as mh
def forward(self, x, targets=None):
    if self.use_pool:
        x = self.pool(x.reshape([0, 3, -1, self.in_channels]).transpose([0, 3, 1, 2]))
    enc = self.ctc_encoder(x)
    out = self.ctc_head(enc, targets)
    return out if not self.training else {"ctc": out, "ctc_neck": enc}
mh.MultiHead.forward = forward
from ppocr.losses import rec_multi_loss as ml
def loss_forward(self, predicts, batch):
    loss = self.loss_funcs["CTCLoss"](predicts["ctc"], batch[:2] + batch[3:])["loss"] * self.weight_1
    self.total_loss = {"CTCLoss": loss, "loss": loss}
    return self.total_loss
ml.MultiLoss.forward = loss_forward
runpy.run_path(script, run_name="__main__")
'''

# Словарь символов внутрь ONNX-файла — так же, как в моделях RapidOCR (тогда отдельный файл не нужен).
ADD_META = r'''
import sys, onnx
model_path, dict_path = sys.argv[1], sys.argv[2]
m = onnx.load(model_path)
chars = open(dict_path, encoding="utf-8").read().splitlines()
meta = m.metadata_props.add()
meta.key, meta.value = "character", "\n".join(chars)
onnx.save(m, model_path)
'''


class TrainError(Exception):
    pass


# ------------------------------------------------------------ пути

def toolkit_dir() -> Path:
    return training.training_dir() / "toolkit"


def active_dir() -> Path:
    path = training.training_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _win() -> bool:
    return sys.platform == "win32"


def env_python() -> Path:
    env = toolkit_dir() / "env"
    return env / ("Scripts/python.exe" if _win() else "bin/python")


def paddleocr_dir() -> Path:
    return toolkit_dir() / "PaddleOCR"


def toolkit_ready() -> bool:
    tk = toolkit_dir()
    return (env_python().exists() and (paddleocr_dir() / "tools" / "train.py").exists()
            and all((tk / "weights" / m["weights"]).exists() for m in MODELS.values())
            and (tk / ".ready").exists())


def active_model(key: str) -> Optional[Path]:
    """Дообученная модель, если она подключена (иначе работает стандартная)."""
    path = active_dir() / f"{key}.onnx"
    return path if path.exists() else None


# ------------------------------------------------------------ запуск процессов

def _run(cmd, log: Path, cwd: Optional[Path] = None, env: Optional[dict] = None,
         on_line: Optional[Callable[[str], None]] = None, cancel: Optional[Callable[[], bool]] = None):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    full_env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", **(env or {}))
    tail = deque(maxlen=12)
    with open(log, "a", encoding="utf-8") as lf:
        lf.write(f"\n$ {' '.join(map(str, cmd))}\n")
        proc = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=full_env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", creationflags=flags)
        for line in proc.stdout:
            lf.write(line)
            if line.strip():
                tail.append(line.rstrip()[:300])
            if on_line:
                on_line(line.rstrip())
            if cancel and cancel():
                proc.kill()
                raise TrainError("Остановлено")
        code = proc.wait()
    if code != 0:
        raise TrainError(f"Команда завершилась с ошибкой ({code}). Подробности: {log}\n\n"
                         "Последние строки журнала:\n" + "\n".join(tail))


def _download(url: str, dst: Path, progress: Progress, title: str):
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                done, last = 0, 0.0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if time.time() - last > 0.5:
                        last = time.time()
                        pct = f" {done * 100 // total}%" if total else ""
                        progress(f"{title}: {done >> 20} МБ{pct}")
            tmp.replace(dst)
            return
        except OSError as e:
            if attempt == 4:
                raise TrainError(f"Не удалось скачать {title}: {e}")
            time.sleep(2 * attempt)


# ------------------------------------------------------------ установка инструментов

def _extract_stripped(arc: Path, dest: Path):
    """Распаковка архива GitHub сразу в dest без верхней папки «PaddleOCR-<commit>».
    Переименование папки после распаковки на Windows падает с «Отказано в доступе»:
    антивирус и индексатор еще держат только что созданные файлы."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with zipfile.ZipFile(arc) as zf:
        for info in zf.infolist():
            rel = info.filename.split("/", 1)[1] if "/" in info.filename else ""
            if not rel:
                continue
            target = (dest / rel).resolve()
            if root != target and root not in target.parents:
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def setup_toolkit(progress: Progress = lambda _m: None, cancel=None):
    """Один раз: uv + Python 3.12 + PaddlePaddle + PaddleOCR + исходные веса моделей (~2 ГБ)."""
    tk = toolkit_dir()
    tk.mkdir(parents=True, exist_ok=True)
    log = tk / "setup.log"
    # 1. uv — установщик Python и пакетов (один файл)
    uv = tk / ("uv.exe" if _win() else "uv")
    if not uv.exists():
        name = "uv-x86_64-pc-windows-msvc.zip" if _win() else "uv-x86_64-unknown-linux-gnu.tar.gz"
        arc = tk / name
        _download(f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/{name}", arc, progress, "uv")
        if name.endswith(".zip"):
            with zipfile.ZipFile(arc) as zf:
                member = next(n for n in zf.namelist() if n.endswith("uv.exe"))
                uv.write_bytes(zf.read(member))
        else:
            import tarfile
            with tarfile.open(arc) as tf:
                member = next(m for m in tf.getmembers() if m.name.endswith("/uv") or m.name == "uv")
                uv.write_bytes(tf.extractfile(member).read())
            uv.chmod(0o755)
        arc.unlink(missing_ok=True)
    uv_env = {"UV_PYTHON_INSTALL_DIR": str(tk / "python"), "UV_CACHE_DIR": str(tk / "uv-cache")}
    # 2. PaddleOCR (код обучения), закреплен на проверенной версии
    if not (paddleocr_dir() / "tools" / "train.py").exists():
        arc = tk / "PaddleOCR.zip"
        urls = [f"https://github.com/PaddlePaddle/PaddleOCR/archive/{PADDLEOCR_COMMIT}.zip",
                f"https://gitee.com/paddlepaddle/PaddleOCR/repository/archive/{PADDLEOCR_COMMIT}.zip"]
        for i, url in enumerate(urls):
            try:
                _download(url, arc, progress, "PaddleOCR")
                break
            except TrainError:
                if i == len(urls) - 1:
                    raise
        progress("Распаковка PaddleOCR")
        _extract_stripped(arc, paddleocr_dir())
        arc.unlink(missing_ok=True)
        for old in tk.glob("PaddleOCR-*"):  # остаток прежней неудачной установки
            shutil.rmtree(old, ignore_errors=True)
    # 3. Python 3.12 и пакеты
    if not env_python().exists():
        progress("Python 3.12 для обучения")
        _run([uv, "venv", tk / "env", "--python", "3.12", "--python-preference", "only-managed"], log,
             env=uv_env, cancel=cancel)
    progress("PaddlePaddle и зависимости (самое долгое — до 1,5 ГБ)")
    _run([uv, "pip", "install", "--python", env_python(), *PADDLE_PKGS, "-r", paddleocr_dir() / "requirements.txt"],
         log, env=uv_env, on_line=lambda s: progress(f"Установка: {s[:90]}") if s.strip() else None, cancel=cancel)
    # 4. исходные веса моделей
    for m in MODELS.values():
        dst = tk / "weights" / m["weights"]
        if not dst.exists():
            _download(PRETRAINED + m["weights"], dst, progress, m["weights"])
    (tk / "runner.py").write_text(RUNNER, encoding="utf-8")
    (tk / "add_meta.py").write_text(ADD_META, encoding="utf-8")
    progress("Проверка PaddlePaddle")
    _run([env_python(), "-c", "import paddle; print(paddle.__version__)"], log, cancel=cancel)
    shutil.rmtree(tk / "uv-cache", ignore_errors=True)  # кэш загрузок больше не нужен
    (tk / ".ready").write_text(datetime.now().isoformat(timespec="seconds"))
    progress("Инструменты обучения установлены")


# ------------------------------------------------------------ данные

def prepare_data(store: training.TrainingStore, progress: Progress = lambda _m: None) -> Dict[str, int]:
    """Строки из проверенных карт → training/dataset; строки дополняются до пропорции 10:1."""
    from PIL import Image

    out = training.training_dir() / "dataset"
    counts = training.build_dataset(store, out_dir=out, progress=progress)
    for img_path in out.glob("*/images/*.png"):
        im = Image.open(img_path).convert("RGB")
        w, h = im.size
        if w < h * PAD_RATIO:
            bg = im.getpixel((w - 1, h // 2))
            canvas = Image.new("RGB", (h * PAD_RATIO, h), bg)
            canvas.paste(im, (0, 0))
            canvas.save(img_path)
    return counts


# ------------------------------------------------------------ обучение одной модели

def _config(key: str, work: Path, data: Path, n_train: int) -> Path:
    import yaml

    m = MODELS[key]
    cfg = yaml.safe_load((paddleocr_dir() / m["config"]).read_text(encoding="utf-8"))
    epochs = max(5, min(30, round(4000 / max(n_train, 1))))
    g = cfg["Global"]
    g.update(use_gpu=False, distributed=False, epoch_num=epochs, print_batch_step=5, save_epoch_step=1000,
             eval_batch_step=[0, 10 ** 9], cal_metric_during_train=True, max_text_length=40,
             pretrained_model=str(toolkit_dir() / "weights" / m["weights"]), save_model_dir=str(work / "out"),
             character_dict_path=str(paddleocr_dir() / m["dict"]), save_res_path=str(work / "pred.txt"))
    cfg["Optimizer"]["lr"].update(learning_rate=m["lr"], warmup_epoch=0)
    tr = cfg["Train"]
    tr["dataset"].update(data_dir=str(data), label_file_list=[str(data / "train.txt")])
    tr["dataset"]["transforms"] = [t for t in tr["dataset"]["transforms"] if "RecConAug" not in t]
    for t in tr["dataset"]["transforms"]:
        if "MultiLabelEncode" in t and t["MultiLabelEncode"]:
            t["MultiLabelEncode"]["max_text_length"] = 40
    tr["sampler"].update(scales=[[48 * PAD_RATIO, 48]], first_bs=16)
    # На Windows процессы загрузки данных стартуют заново (spawn) и повторно выполняют скрипт
    # обучения — загрузка в основном процессе надежнее.
    tr["loader"].update(batch_size_per_card=16, num_workers=0 if sys.platform == "win32" else 2,
                        use_shared_memory=False)
    ev = cfg["Eval"]
    ev["dataset"].update(data_dir=str(data), label_file_list=[str(data / ("val.txt" if (data / "val.txt").exists()
                                                                            else "train.txt"))])
    ev["loader"].update(batch_size_per_card=16, num_workers=0)
    path = work / "config.yml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path, epochs


def train_model(key: str, progress: Progress = lambda _m: None, cancel=None) -> Path:
    """Дообучает одну модель → путь к новому ONNX (еще не подключен)."""
    data = training.training_dir() / "dataset" / key
    train_txt = data / "train.txt"
    n_train = len(train_txt.read_text(encoding="utf-8").splitlines()) if train_txt.exists() else 0
    if n_train < MIN_LINES:
        raise TrainError(f"{MODELS[key]['title']}: мало строк ({n_train}, нужно от {MIN_LINES})")
    work = training.training_dir() / "work" / key
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    cfg, epochs = _config(key, work, data, n_train)
    total = epochs * math.ceil(n_train / 16)
    log = work / "train.log"
    title = MODELS[key]["title"]
    started = time.time()

    def on_line(line: str):
        m = re.search(r"global_step: (\d+)", line)
        if m:
            step = int(m.group(1))
            spent = time.time() - started
            left = spent / max(step, 1) * max(total - step, 0)
            progress(f"{title}: шаг {step} из {total} · осталось ~{int(left // 60)} мин")

    progress(f"{title}: обучение ({n_train} строк, {epochs} проходов)")
    tk = toolkit_dir()
    _run([env_python(), tk / "runner.py", "tools/train.py", "-c", cfg], log, cwd=paddleocr_dir(),
         on_line=on_line, cancel=cancel)
    progress(f"{title}: сохранение модели")
    _run([env_python(), "tools/export_model.py", "-c", cfg, "-o", f"Global.pretrained_model={work / 'out' / 'latest'}",
          f"Global.save_inference_dir={work / 'infer'}"], log, cwd=paddleocr_dir(), cancel=cancel)
    p2o = env_python().parent / ("paddle2onnx.exe" if _win() else "paddle2onnx")
    onnx_path = work / "model.onnx"
    _run([p2o, "--model_dir", work / "infer", "--model_filename", "inference.json",
          "--params_filename", "inference.pdiparams", "--save_file", onnx_path, "--opset_version", "14"],
         log, cancel=cancel)
    _run([env_python(), tk / "add_meta.py", onnx_path, paddleocr_dir() / MODELS[key]["dict"]], log, cancel=cancel)
    return onnx_path


# ------------------------------------------------------------ проверка и подключение

def accuracy(key: str, onnx_path: Path) -> float:
    """Доля строк проверочных карт, прочитанных полностью верно (пробелы тоже важны)."""
    import numpy as np
    from PIL import Image
    from rapidocr import RapidOCR

    from .ocr.ocr_engine import latin_params, rapidocr_params

    data = training.training_dir() / "dataset" / key
    val = data / "val.txt"
    if not val.exists():
        return 0.0
    params = latin_params() if key == training.LATIN else rapidocr_params()
    params["Rec.model_path"] = str(onnx_path)
    engine = RapidOCR(params=params)
    rows = [r.split("\t", 1) for r in val.read_text(encoding="utf-8").splitlines() if "\t" in r]
    ok = 0
    for rel, text in rows:
        img = np.array(Image.open(data / rel).convert("RGB"))[:, :, ::-1]
        res = engine(img, use_det=False, use_cls=False, use_rec=True)
        got = (res.txts or [""])[0] if res is not None else ""
        ok += " ".join(got.split()) == " ".join(text.split())
    return ok / len(rows) if rows else 0.0


def current_model(key: str) -> Path:
    from .ocr.ocr_engine import LAT_MODEL, REC_MODEL, models_dir

    active = active_model(key)
    if active:
        return active
    return models_dir() / (LAT_MODEL if key == training.LATIN else REC_MODEL)


def history() -> dict:
    path = active_dir() / "history.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def install(key: str, onnx_path: Path, before: float, after: float):
    """Подключить новую модель; прежняя дообученная (если была) сохраняется для отката."""
    dst = active_dir() / f"{key}.onnx"
    if dst.exists():
        shutil.copy2(dst, active_dir() / f"{key}.prev.onnx")
    shutil.copy2(onnx_path, dst)
    h = history()
    h[key] = {"date": datetime.now().isoformat(timespec="minutes"), "before": before, "after": after}
    (active_dir() / "history.json").write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
    _reset_engine()


def rollback(key: str) -> str:
    """Вернуть прежнюю модель: предыдущую дообученную, а если ее нет — стандартную."""
    dst = active_dir() / f"{key}.onnx"
    prev = active_dir() / f"{key}.prev.onnx"
    if prev.exists():
        prev.replace(dst)
        msg = "возвращена предыдущая дообученная модель"
    else:
        dst.unlink(missing_ok=True)
        msg = "возвращена стандартная модель"
    h = history()
    h.pop(key, None)
    (active_dir() / "history.json").write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
    _reset_engine()
    return msg


def _reset_engine():
    from .ocr.ocr_engine import OcrEngine
    with OcrEngine._lock:
        OcrEngine._instance = None


def train_all(store: training.TrainingStore, progress: Progress = lambda _m: None, cancel=None) -> dict:
    """Весь цикл: данные → обучение → проверка → подключение, если стало лучше. Возвращает итоги по моделям."""
    if not toolkit_ready():
        raise TrainError("Сначала установите инструменты обучения")
    counts = prepare_data(store, progress)
    results = {}
    for key in MODELS:
        n = counts.get(f"{key}_train", 0)
        if n < MIN_LINES:
            results[key] = {"skipped": f"мало строк для обучения ({n}, нужно от {MIN_LINES})"}
            continue
        new = train_model(key, progress, cancel)
        progress(f"{MODELS[key]['title']}: проверка на отложенных картах")
        before = accuracy(key, current_model(key))
        after = accuracy(key, new)
        better = after > before or (after == before and after > 0)
        if better:
            install(key, new, before, after)
        results[key] = {"before": before, "after": after, "installed": better,
                        "train": n, "val": counts.get(f"{key}_val", 0)}
    return results
