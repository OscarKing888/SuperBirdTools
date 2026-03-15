@echo off
REM 自动安装 PyTorch CUDA 版本
chcp 65001 >nul
echo ============================================================
echo   PyTorch CUDA 版本自动安装脚本
echo ============================================================
echo.

REM 检查是否在虚拟环境中
if exist ".venv\Scripts\activate.bat" (
    echo [1/5] 激活虚拟环境...
    call .venv\Scripts\activate.bat
    echo ✅ 虚拟环境已激活
) else (
    echo ⚠️  未找到虚拟环境，将在系统 Python 中安装
    echo    建议先创建虚拟环境: python -m venv .venv
    pause
)

echo.
echo [2/5] 检测 NVIDIA GPU 和 CUDA 驱动...
echo.

REM 检查 nvidia-smi 是否可用
where nvidia-smi >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ 未找到 nvidia-smi 命令
    echo.
    echo 💡 请先安装 NVIDIA 驱动程序:
    echo    1. 访问 https://www.nvidia.com/drivers
    echo    2. 下载并安装最新的驱动程序
    echo    3. 安装完成后重新运行此脚本
    echo.
    pause
    exit /b 1
)

echo ✅ 找到 NVIDIA 驱动程序
echo.

REM 获取驱动版本（Windows 批处理中不需要 head，for /f 会自动取第一行）
set DRIVER_VERSION=
for /f "tokens=* delims=" %%i in ('nvidia-smi --query-gpu=driver_version --format=csv,noheader,nounits 2^>nul') do (
    if not defined DRIVER_VERSION set DRIVER_VERSION=%%i
)
if defined DRIVER_VERSION (
    echo    驱动版本: %DRIVER_VERSION%
) else (
    echo    驱动版本: 无法检测
)

REM 检测 CUDA 版本（通过 nvidia-smi）
set CUDA_VERSION=
for /f "tokens=* delims=" %%i in ('nvidia-smi --query-gpu=cuda_version --format=csv,noheader,nounits 2^>nul') do (
    if not defined CUDA_VERSION set CUDA_VERSION=%%i
)
if defined CUDA_VERSION (
    echo    CUDA 版本: %CUDA_VERSION%
) else (
    echo    CUDA 版本: 无法检测
)

echo.
echo [3/5] 检测当前 PyTorch 版本...
python -c "import torch; print(f'当前版本: {torch.__version__}'); print(f'CUDA 可用: {torch.cuda.is_available()}')" 2>nul
if %errorlevel% neq 0 (
    echo ⚠️  PyTorch 未安装
)

echo.
echo [4/5] 选择 PyTorch CUDA 版本...
echo.

REM 根据检测到的 CUDA 版本选择 PyTorch 版本
REM 如果 CUDA 版本 >= 12.1，使用 cu121
REM 如果 CUDA 版本 >= 11.8，使用 cu118
REM 否则使用 cu118（兼容性最好）

set PYTORCH_CUDA=cu118
set PYTORCH_INDEX=https://download.pytorch.org/whl/cu118

REM 尝试解析 CUDA 版本号
echo    检测到的 CUDA 版本: %CUDA_VERSION%
echo    推荐使用 PyTorch CUDA 11.8 版本（兼容性最好）
echo.

echo [5/5] 卸载旧版本并安装 PyTorch CUDA 版本...
echo.

set pytorch_cache=d:\pytorch_cache
REM 设置缓存目录
set CACHE_DIR=%pytorch_cache%
if not exist "%CACHE_DIR%" mkdir "%CACHE_DIR%"
echo    缓存目录: %CD%\%CACHE_DIR%

REM 初始化变量
set USE_CACHE=0
set INSTALL_SUCCESS=1

REM 检查缓存
echo    检查本地缓存...
dir /b "%CACHE_DIR%\torch*.whl" >nul 2>&1
if %errorlevel% equ 0 (
    echo    ✅ 找到缓存文件
    for %%f in ("%CACHE_DIR%\torch*.whl") do echo       - %%~nxf
    for %%f in ("%CACHE_DIR%\torchvision*.whl") do echo       - %%~nxf
    for %%f in ("%CACHE_DIR%\torchaudio*.whl") do echo       - %%~nxf
    set USE_CACHE=1
) else (
    echo    ⚠️  未找到缓存文件，将从网络下载
    set USE_CACHE=0
)

REM 卸载旧版本
echo.
echo    正在卸载旧版本...
pip uninstall torch torchvision torchaudio -y >nul 2>&1

REM 安装 CUDA 版本
echo.
if "%USE_CACHE%"=="1" (
    echo    正在从缓存安装 PyTorch CUDA 11.8 版本...
    echo    如果缓存不完整，将从网络补充下载...
    echo.
    pip install torch torchvision torchaudio --find-links "%CACHE_DIR%" --index-url https://download.pytorch.org/whl/cu118
    set INSTALL_SUCCESS=%errorlevel%
    if %INSTALL_SUCCESS% neq 0 (
        echo.
        echo    ⚠️  缓存安装失败，尝试从网络直接安装...
        pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
        set INSTALL_SUCCESS=%errorlevel%
    )
    REM 如果安装成功，确保缓存已更新
    if %INSTALL_SUCCESS% equ 0 (
        echo.
        echo    正在更新缓存...
        pip download torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118 --dest "%CACHE_DIR%" >nul 2>&1
    )
) else (
    echo    正在安装 PyTorch CUDA 11.8 版本...
    echo    这可能需要几分钟，请耐心等待...
    echo    同时下载到缓存目录以便下次使用...
    echo.
    REM 先下载到缓存
    pip download torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118 --dest "%CACHE_DIR%"
    if %errorlevel% equ 0 (
        echo    ✅ 已下载到缓存
        echo.
        echo    正在从缓存安装（缺失时从网络补充）...
        pip install torch torchvision torchaudio --find-links "%CACHE_DIR%" --index-url https://download.pytorch.org/whl/cu118
        set INSTALL_SUCCESS=%errorlevel%
    ) else (
        echo    ⚠️  下载失败，直接从网络安装...
        pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
        set INSTALL_SUCCESS=%errorlevel%
        REM 安装成功后尝试下载到缓存
        if %INSTALL_SUCCESS% equ 0 (
            echo.
            echo    正在更新缓存...
            pip download torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118 --dest "%CACHE_DIR%" >nul 2>&1
        )
    )
)

REM 检查安装是否成功（确保变量已定义）
if not defined INSTALL_SUCCESS set INSTALL_SUCCESS=1
if %INSTALL_SUCCESS% neq 0 (
    echo.
    echo ❌ 安装失败
    echo.
    echo 💡 如果安装失败，可以尝试:
    echo    1. 手动安装: pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
    echo    2. 或使用 CUDA 12.1: pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   验证安装...
echo ============================================================
echo.

python -c "import torch; print(f'✅ PyTorch 版本: {torch.__version__}'); print(f'✅ CUDA 可用: {torch.cuda.is_available()}'); print(f'✅ CUDA 版本: {torch.version.cuda if torch.cuda.is_available() else \"N/A\"}'); print(f'✅ GPU 数量: {torch.cuda.device_count() if torch.cuda.is_available() else 0}'); [print(f'✅ GPU {i}: {torch.cuda.get_device_name(i)}') for i in range(torch.cuda.device_count())] if torch.cuda.is_available() else None"

if %errorlevel% neq 0 (
    echo.
    echo ⚠️  验证失败，但安装可能已成功
    echo    请手动运行: python -c "import torch; print(torch.cuda.is_available())"
) else (
    echo.
    echo ============================================================
    echo   ✅ 安装完成！
    echo ============================================================
    echo.
    echo   现在可以重新运行程序，应该会使用 CUDA 加速了
    echo.
)

pause
