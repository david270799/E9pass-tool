; Установщик E9pay (Inno Setup 6). Собирается в GitHub Actions: .github/workflows/build-windows.yml
; Ставится в профиль пользователя — права администратора не нужны.

#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{6F1B5C2E-9A3D-4E7B-8C21-0E9A7C000001}
AppName=E9pay
AppVersion={#AppVersion}
AppPublisher=E9pay
DefaultDirName={localappdata}\Programs\E9pay
DefaultGroupName=E9pay
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=E9pay-Setup-{#AppVersion}
SetupIconFile=..\e9pass\assets\logo.ico
UninstallDisplayIcon={app}\E9pay.exe
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
DiskSpanning=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"

[Files]
Source: "..\dist\E9pay\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\E9pay"; Filename: "{app}\E9pay.exe"
Name: "{group}\Удалить E9pay"; Filename: "{uninstallexe}"
Name: "{userdesktop}\E9pay"; Filename: "{app}\E9pay.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\E9pay.exe"; Description: "Запустить E9pay"; Flags: nowait postinstall skipifsilent

; Папка данных (D:\E9pass или %USERPROFILE%\E9pass) при удалении программы НЕ удаляется.
