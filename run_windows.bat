@echo off
cd /d %~dp0
python -c "import fastapi, uvicorn, sqlalchemy, jinja2, multipart, itsdangerous" >nul 2>&1
if errorlevel 1 (
  echo Instalando dependencias da primeira execucao...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Nao foi possivel instalar as dependencias. Verifique sua conexao com a internet e tente novamente.
    pause
    exit /b 1
  )
)
echo.
echo QS Ajuda de Custos V0.9.2 iniciado em http://127.0.0.1:8000
echo O banco persistente das versoes anteriores sera reutilizado automaticamente.
echo Para encerrar, pressione CTRL+C nesta janela.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
