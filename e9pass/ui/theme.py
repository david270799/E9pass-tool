"""Темная тема: черный фон, белый текст и контуры, желтый акцент."""

BG = "#121212"
SURFACE = "#1E1E1E"
SURFACE_2 = "#181818"
TEXT = "#FFFFFF"
MUTED = "#B3B3B3"
DIM = "#8C8C8C"
LINE = "rgba(255,255,255,0.14)"
ACCENT = "#F5C518"

QSS = f"""
* {{ font-family: "Segoe UI", "Malgun Gothic", "Noto Sans KR", sans-serif; font-size: 14px; color: {TEXT}; }}
QMainWindow, QDialog, #Page {{ background: {BG}; }}
QWidget#Sidebar {{ background: {SURFACE_2}; border-right: 1px solid rgba(255,255,255,0.10); }}
QLabel {{ background: transparent; }}
QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel[dim="true"] {{ color: {DIM}; font-size: 12px; }}
QLabel[h1="true"] {{ font-size: 24px; font-weight: 800; }}
QLabel[h2="true"] {{ font-size: 16px; font-weight: 700; }}
QLabel[caps="true"] {{ color: {MUTED}; font-size: 12px; font-weight: 700; letter-spacing: 1px; }}
QLabel[warn="true"] {{ color: {ACCENT}; }}
QLabel[mono="true"] {{ font-family: "Consolas", "JetBrains Mono", monospace; }}

QFrame[card="true"] {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 14px; }}
QFrame[card="true"][selected="true"] {{ border: 1px solid {TEXT}; }}
QFrame[card="true"]:hover {{ border: 1px solid rgba(255,255,255,0.45); }}
QFrame[panel="true"] {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 14px; }}
QFrame[warnbox="true"] {{ background: transparent; border: 1.5px solid {ACCENT}; border-radius: 10px; }}
QFrame[drop="true"] {{ background: #161616; border: 1.5px dashed {TEXT}; border-radius: 14px; }}
QFrame[drop="true"][hover="true"] {{ border: 2px dashed {ACCENT}; background: #1d1a0c; }}

QPushButton {{ background: transparent; border: 1px solid {TEXT}; border-radius: 10px; padding: 0 16px;
               min-height: 40px; font-weight: 600; }}
QPushButton:hover {{ background: rgba(255,255,255,0.08); }}
QPushButton:disabled {{ color: {DIM}; border-color: #444; }}
QPushButton[primary="true"] {{ background: {ACCENT}; color: #121212; border: none; font-weight: 800; }}
QPushButton[primary="true"]:hover {{ background: #FFD43B; }}
QPushButton[primary="true"]:disabled {{ background: #6b5a1a; color: #2a2a2a; }}
QPushButton[flat="true"] {{ border: none; min-height: 32px; padding: 0 8px; }}
QPushButton[chip="true"] {{ min-height: 34px; border-color: rgba(255,255,255,0.3); font-size: 13px; }}
QPushButton[chip="true"]:checked {{ background: {TEXT}; color: #121212; border-color: {TEXT}; }}
QPushButton[small="true"] {{ min-height: 30px; padding: 0 10px; font-size: 12.5px; }}
QPushButton#NavButton {{ border: none; border-radius: 10px; text-align: left; padding: 10px 14px; min-height: 44px;
                         color: #D9D9D9; font-weight: 600; }}
QPushButton#NavButton:checked {{ background: {ACCENT}; color: #121212; font-weight: 800; }}
QPushButton#NavButton:hover:!checked {{ background: rgba(255,255,255,0.06); }}

QLineEdit, QComboBox, QSpinBox {{ background: #141414; border: 1px solid rgba(255,255,255,0.22); border-radius: 8px;
                                  min-height: 36px; padding: 0 10px; selection-background-color: {ACCENT};
                                  selection-color: #121212; }}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit[warn="true"] {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {LINE}; selection-background-color: {ACCENT};
                               selection-color: #121212; outline: none; }}

QCheckBox, QRadioButton {{ spacing: 10px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; }}
QCheckBox::indicator {{ border: 1.5px solid {TEXT}; border-radius: 4px; background: transparent; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: none; }}
QRadioButton::indicator {{ border: 1.5px solid {TEXT}; border-radius: 9px; }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QSlider::groove:horizontal {{ height: 4px; background: #3A3A3A; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}

QProgressBar {{ background: #2E2E2E; border: none; border-radius: 3px; max-height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #3A3A3A; border-radius: 5px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: #3A3A3A; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}

QTreeWidget, QTableWidget, QListWidget {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 12px;
    alternate-background-color: #222; outline: none; }}
QTreeWidget::item, QTableWidget::item, QListWidget::item {{ padding: 8px 6px; border-bottom: 1px solid rgba(255,255,255,0.06); }}
QTreeWidget::item:selected, QTableWidget::item:selected, QListWidget::item:selected {{
    background: #2A2A2A; color: {TEXT}; }}
QHeaderView::section {{ background: {SURFACE}; color: {DIM}; border: none; border-bottom: 1px solid {LINE};
                        padding: 8px 6px; font-size: 12px; font-weight: 600; }}
QToolTip {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {LINE}; padding: 6px; }}
QMessageBox {{ background: {SURFACE}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {LINE}; }}
QMenu::item:selected {{ background: {ACCENT}; color: #121212; }}
QGraphicsView {{ background: #0E0E0E; border: 1px solid {LINE}; border-radius: 12px; }}
"""
