#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动安装 PyTorch CUDA 版本
检测系统 CUDA 版本并安装对应的 PyTorch
"""

import os
import sys
import subprocess
import platform
import glob
from pathlib import Path

# Windows 控制台编码设置
if sys.platform == 'win32':
    try:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

def check_nvidia_driver():
    """检查 NVIDIA 驱动是否安装"""
    try:
        result = subprocess.run(['nvidia-smi'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return True, result.stdout
        return False, None
    except FileNotFoundError:
        return False, None
    except Exception as e:
        return False, str(e)

def get_cuda_version_from_nvidia_smi():
    """从 nvidia-smi 获取 CUDA 版本"""
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=cuda_version', '--format=csv,noheader,nounits'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            version = result.stdout.strip().split('\n')[0]
            return version
    except Exception:
        pass
    return None

def get_driver_version():
    """获取驱动版本"""
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader,nounits'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            version = result.stdout.strip().split('\n')[0]
            return version
    except Exception:
        pass
    return None

def check_current_pytorch():
    """检查当前 PyTorch 版本"""
    try:
        import torch
        version = torch.__version__
        cuda_available = torch.cuda.is_available()
        cuda_version = torch.version.cuda if cuda_available else None
        return True, version, cuda_available, cuda_version
    except ImportError:
        return False, None, False, None
    except Exception as e:
        return False, None, False, str(e)

def check_python_version():
    """检查Python版本并给出兼容性建议"""
    version = sys.version_info
    version_str = f"{version.major}.{version.minor}"
    
    # PyTorch官方支持的Python版本（截至2025年）
    # Python 3.14可能还没有预编译的wheel包
    if version.major == 3 and version.minor >= 14:
        return False, version_str, "PyTorch可能尚未为Python 3.14提供预编译wheel包"
    elif version.major == 3 and version.minor >= 12:
        return True, version_str, None
    else:
        return True, version_str, None

def get_cache_dir():
    """获取缓存目录路径"""
    # 使用项目根目录下的 pytorch_cache 目录
    script_dir = Path(__file__).parent.parent
    cache_dir = script_dir / 'pytorch_cache'
    return cache_dir

def ensure_cache_dir():
    """确保缓存目录存在"""
    cache_dir = get_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir

def check_cache_for_packages(cache_dir, packages=['torch', 'torchvision', 'torchaudio']):
    """检查缓存目录中是否有指定的包文件"""
    if not cache_dir.exists():
        return False, []
    
    found_files = []
    for package in packages:
        # 查找以包名开头的 whl 文件
        pattern = str(cache_dir / f'{package}*.whl')
        files = glob.glob(pattern)
        if files:
            found_files.extend(files)
    
    # 如果找到至少一个包的文件，认为缓存可用
    has_cache = len(found_files) > 0
    return has_cache, found_files

def download_packages_to_cache(cuda_version='cu118', index_url=None, cache_dir=None):
    """下载包到缓存目录"""
    if index_url is None:
        index_url = f'https://download.pytorch.org/whl/{cuda_version}'
    
    if cache_dir is None:
        cache_dir = ensure_cache_dir()
    
    print(f"   📥 正在下载包到缓存目录: {cache_dir}")
    print(f"      索引 URL: {index_url}")
    
    try:
        # 使用 pip download 下载包到缓存目录
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'download', 'torch', 'torchvision', 'torchaudio',
             '--index-url', index_url,
             '--dest', str(cache_dir)],
            check=True
        )
        print("   ✅ 下载完成")
        return True
    except subprocess.CalledProcessError as e:
        print(f"   ❌ 下载失败: {e}")
        return False

def select_pytorch_cuda_version(cuda_version_str):
    """根据系统 CUDA 版本选择 PyTorch CUDA 版本"""
    if not cuda_version_str:
        # 默认使用 cu118（兼容性最好）
        return 'cu118', 'https://download.pytorch.org/whl/cu118'
    
    try:
        # 解析版本号（例如 "12.1" -> 12.1）
        major, minor = map(int, cuda_version_str.split('.')[:2])
        version_float = major + minor / 10.0
        
        if version_float >= 12.1:
            return 'cu121', 'https://download.pytorch.org/whl/cu121'
        elif version_float >= 11.8:
            return 'cu118', 'https://download.pytorch.org/whl/cu118'
        else:
            # 旧版本 CUDA，使用 cu118（向后兼容）
            return 'cu118', 'https://download.pytorch.org/whl/cu118'
    except Exception:
        # 解析失败，使用默认
        return 'cu118', 'https://download.pytorch.org/whl/cu118'

def install_pytorch_cuda(cuda_version='cu118', index_url=None, use_cache=True):
    """安装 PyTorch CUDA 版本"""
    if index_url is None:
        index_url = f'https://download.pytorch.org/whl/{cuda_version}'
    
    print(f"\n📦 正在安装 PyTorch CUDA 版本 ({cuda_version})...")
    print(f"   索引 URL: {index_url}")
    print("   这可能需要几分钟，请耐心等待...\n")
    
    cache_dir = ensure_cache_dir()
    has_cache = False
    cache_files = []
    
    # 检查缓存
    if use_cache:
        print("   0. 检查本地缓存...")
        has_cache, cache_files = check_cache_for_packages(cache_dir)
        if has_cache:
            print(f"   ✅ 找到缓存文件 ({len(cache_files)} 个)")
            for f in cache_files[:3]:  # 只显示前3个
                print(f"      - {os.path.basename(f)}")
            if len(cache_files) > 3:
                print(f"      ... 还有 {len(cache_files) - 3} 个文件")
        else:
            print("   ⚠️  未找到缓存文件，将从网络下载")
    
    # 卸载旧版本
    print("\n   1. 卸载旧版本...")
    try:
        subprocess.run([sys.executable, '-m', 'pip', 'uninstall', 'torch', 'torchvision', 'torchaudio', '-y'],
                      capture_output=True, check=False)
    except Exception:
        pass
    
    # 安装新版本
    print(f"\n   2. 安装 PyTorch CUDA {cuda_version} 版本...")
    
    # 构建安装命令
    install_cmd = [sys.executable, '-m', 'pip', 'install', 'torch', 'torchvision', 'torchaudio']
    
    if has_cache and use_cache:
        # 优先使用缓存，如果缓存中没有再从网络下载
        print("      优先使用本地缓存...")
        install_cmd.extend(['--find-links', str(cache_dir), '--index-url', index_url])
    else:
        # 直接从网络安装，同时保存到缓存
        install_cmd.extend(['--index-url', index_url])
        # 下载到缓存以便下次使用
        if use_cache:
            print("      同时下载到缓存目录以便下次使用...")
            download_success = download_packages_to_cache(cuda_version, index_url, cache_dir)
            if download_success:
                # 下载成功后，从缓存安装（优先使用缓存，缺失时从网络）
                install_cmd = [sys.executable, '-m', 'pip', 'install', 'torch', 'torchvision', 'torchaudio',
                              '--find-links', str(cache_dir), '--index-url', index_url]
    
    try:
        result = subprocess.run(install_cmd, check=True)
        print("   ✅ 安装成功！")
        
        # 如果安装成功且使用了网络，确保缓存已更新
        if use_cache:
            # 检查是否需要更新缓存（如果从网络安装但缓存中没有）
            if not has_cache or len(cache_files) < 3:  # 至少需要3个包文件
                print("   💾 正在更新缓存...")
                download_packages_to_cache(cuda_version, index_url, cache_dir)
            else:
                print("   💾 缓存已就绪，下次安装将使用缓存")
        
        return True
    except subprocess.CalledProcessError as e:
        print(f"   ❌ 安装失败: {e}")
        # 如果使用缓存失败，尝试直接从网络安装
        if has_cache and use_cache:
            print("   🔄 缓存安装失败，尝试从网络直接安装...")
            try:
                result = subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', 'torch', 'torchvision', 'torchaudio',
                     '--index-url', index_url],
                    check=True
                )
                print("   ✅ 从网络安装成功！")
                # 下载到缓存以便下次使用
                if use_cache:
                    print("   💾 正在更新缓存...")
                    download_packages_to_cache(cuda_version, index_url, cache_dir)
                return True
            except subprocess.CalledProcessError as e2:
                print(f"   ❌ 网络安装也失败: {e2}")
                return False
        return False

def verify_installation():
    """验证安装"""
    print("\n" + "=" * 60)
    print("验证安装...")
    print("=" * 60)
    
    try:
        import torch
        print(f"✅ PyTorch 版本: {torch.__version__}")
        print(f"✅ CUDA 可用: {torch.cuda.is_available()}")
        
        if torch.cuda.is_available():
            print(f"✅ CUDA 版本: {torch.version.cuda}")
            print(f"✅ GPU 数量: {torch.cuda.device_count()}")
            for i in range(torch.cuda.device_count()):
                print(f"✅ GPU {i}: {torch.cuda.get_device_name(i)}")
            return True
        else:
            print("⚠️  CUDA 不可用")
            return False
    except ImportError:
        print("❌ PyTorch 未正确安装")
        return False
    except Exception as e:
        print(f"❌ 验证失败: {e}")
        return False

def main():
    print("=" * 60)
    print("  PyTorch CUDA 版本自动安装脚本")
    print("=" * 60)
    print()
    
    # 步骤 0: 检查Python版本
    print("[0/6] 检查Python版本...")
    compatible, py_version, warning = check_python_version()
    print(f"   Python版本: {sys.version.split()[0]}")
    if not compatible:
        print(f"   ⚠️  {warning}")
        print("   PyTorch可能不支持此Python版本")
    else:
        print("   ✅ Python版本兼容")
    print()
    
    # 步骤 1: 检查 NVIDIA 驱动
    print("[1/6] 检查 NVIDIA GPU 和驱动...")
    driver_available, driver_info = check_nvidia_driver()
    
    if not driver_available:
        print("❌ 未找到 NVIDIA 驱动程序")
        print()
        print("💡 请先安装 NVIDIA 驱动程序:")
        print("   1. 访问 https://www.nvidia.com/drivers")
        print("   2. 下载并安装最新的驱动程序")
        print("   3. 安装完成后重新运行此脚本")
        print()
        input("按 Enter 键退出...")
        return False
    
    print("✅ 找到 NVIDIA 驱动程序")
    
    driver_version = get_driver_version()
    if driver_version:
        print(f"   驱动版本: {driver_version}")
    
    cuda_version = get_cuda_version_from_nvidia_smi()
    if cuda_version:
        print(f"   CUDA 版本: {cuda_version}")
    else:
        print("   ⚠️  无法检测 CUDA 版本，将使用默认版本")
    
    # 步骤 2: 检查当前 PyTorch
    print()
    print("[2/6] 检查当前 PyTorch 版本...")
    pytorch_installed, pytorch_version, cuda_available, pytorch_cuda_version = check_current_pytorch()
    
    if pytorch_installed:
        print(f"   当前版本: {pytorch_version}")
        print(f"   CUDA 可用: {cuda_available}")
        if pytorch_cuda_version:
            print(f"   PyTorch CUDA 版本: {pytorch_cuda_version}")
        
        if cuda_available:
            print()
            print("✅ PyTorch 已安装 CUDA 版本，无需重新安装")
            verify_installation()
            return True
    else:
        print("   ⚠️  PyTorch 未安装")
    
    # 步骤 3: 选择 PyTorch CUDA 版本
    print()
    print("[3/6] 选择 PyTorch CUDA 版本...")
    pytorch_cuda, index_url = select_pytorch_cuda_version(cuda_version)
    print(f"   将安装: PyTorch CUDA {pytorch_cuda} 版本")
    print(f"   索引 URL: {index_url}")
    
    # 步骤 4: 确认安装
    print()
    print("[4/6] 准备安装...")
    print("   将卸载旧版本并安装新版本")
    response = input("   是否继续? (Y/n): ").strip().lower()
    if response and response != 'y':
        print("   已取消")
        return False
    
    # 步骤 5: 安装
    print()
    print("[5/6] 安装 PyTorch CUDA 版本...")
    cache_dir = get_cache_dir()
    print(f"   缓存目录: {cache_dir}")
    success = install_pytorch_cuda(pytorch_cuda, index_url, use_cache=True)
    
    if not success:
        print()
        print("❌ 安装失败")
        print()
        print("💡 可以尝试手动安装:")
        print(f"   pip install torch torchvision torchaudio --index-url {index_url}")
        print()
        input("按 Enter 键退出...")
        return False
    
    # 验证
    print()
    success = verify_installation()
    
    if success:
        print()
        print("=" * 60)
        print("  ✅ 安装完成！")
        print("=" * 60)
        print()
        print("  现在可以重新运行程序，应该会使用 CUDA 加速了")
        print()
    else:
        print()
        print("⚠️  安装完成，但验证失败")
        print("   请手动运行: python -c \"import torch; print(torch.cuda.is_available())\"")
        print()
    
    input("按 Enter 键退出...")
    return success

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n已取消")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()
        input("\n按 Enter 键退出...")
        sys.exit(1)
