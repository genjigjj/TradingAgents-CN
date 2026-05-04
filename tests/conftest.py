import os
import sys

import pytest

# 将项目根目录加入 sys.path，确保 `import tradingagents` 可用
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture
def anyio_backend():
    """指定 anyio 使用 asyncio 后端（而非 trio）"""
    return "asyncio"

