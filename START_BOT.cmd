@echo off
chcp 65001 > nul
set PYTHONUTF8=1
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Ошибка: окружение Python не найдено.
  echo Обратитесь к инструкции в README.md.
  pause
  exit /b 1
)

findstr /C:"PASTE_BOTFATHER_TOKEN_HERE" ".env" > nul
if not errorlevel 1 (
  echo Сейчас откроется файл .env.
  echo Вставьте токен от BotFather после BOT_TOKEN=, сохраните файл и закройте Блокнот.
  echo Telegram ID вводить не нужно.
  start "" /wait notepad.exe ".env"
  findstr /C:"PASTE_BOTFATHER_TOKEN_HERE" ".env" > nul
  if not errorlevel 1 (
    echo Токен не был вставлен. Бот не запущен.
    pause
    exit /b 1
  )
)

echo Запускаю Telegram-бота...
".venv\Scripts\python.exe" bot.py
echo.
echo Бот остановлен. Выше показана причина, если произошла ошибка.
pause
