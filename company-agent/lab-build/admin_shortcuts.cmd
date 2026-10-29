@echo off
set "URL=http://10.0.40.50:8080"
set "EDGE=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
set "LNK=%USERPROFILE%\Desktop\IT 운영 에이전트.url"
set "BOOT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\it-agent-dashboard.cmd"
if not exist "%EDGE%" echo NO-EDGE
> "%LNK%" echo [InternetShortcut]
>> "%LNK%" echo URL=%URL%
>> "%LNK%" echo IconFile=%EDGE%
>> "%LNK%" echo IconIndex=0
> "%BOOT%" echo @echo off
>> "%BOOT%" echo start "" "%EDGE%" --app=%URL% --start-maximized
curl.exe -s -o nul -w "dashboard HTTP %%{http_code}\n" %URL%/api/state
if exist "%LNK%" echo desktop shortcut OK
if exist "%BOOT%" echo startup launcher OK
