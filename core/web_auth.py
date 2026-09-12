"""WebUI 密码哈希：Argon2id（OWASP 2024 推荐参数，内存硬化抗 GPU 暴力破解）。

存盘格式：argon2-cffi 默认输出 ``$argon2id$v=19$m=65536,t=3,p=4$<saltB64>$<hashB64>``

明文只存在于：（1）operator 配置明文密码时的临时内存；（2）首次启动生成的临时密码一次性打印到日志。

校验：argon2-cffi 内部使用恒定时间比较。

旧 PBKDF2 哈希（``pbkdf2$...``）不再被识别：Shangbanzu._start_webui 见到该前缀会
按「未配置密码」处理，重新生成一次性临时密码并打印到启动日志。
"""

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError, VerifyMismatchError

# OWASP Password Storage Cheat Sheet (2024) 推荐：
#   m=19 MiB, t=2, p=1  或  m=12 MiB, t=3, p=1
# 这里偏保守取 m=64MiB, t=3, p=4：登录路径 QPS 极低（< 1），换来的破解成本
# 高出一个数量级。
#
# 实测成本（本仓库审计机：8 核 / Python 3.13 / argon2-cffi 默认后端，各 7 次采样）：
#   单次哈希 ≈ 70ms，单次校验 ≈ 77ms，区间 68~95ms。
# 该值随 CPU 与实时负载浮动很大 —— 截图渲染同时跑 3 个 Chromium 时这些核心
# 被占满，登录校验会明显变慢（弱 CPU 上是 150~300ms 量级）。所以上层的排队
# 上限与限流一律按最坏情况估，不要按「一次 50ms」这类乐观值算容量。
#
# 线程与内存预算：parallelism=4 时 libargon2 每次哈希会额外起 4 个线程、瞬时
# 占用 64MiB；webui/server/_core.py 用 Semaphore(2) 限并发（未鉴权路径不能
# 无限并发），因此峰值是 8 个 Argon2 线程 + 128MiB 瞬时内存。这与 Playwright
# 的 3 个 Chromium 共享同一批 CPU 核心，登录高峰会和出图互相抢 CPU ——
# 并发闸只开 2 就是为了这个。
_PH = PasswordHasher(memory_cost=65536, time_cost=3, parallelism=4)


def hash_password(plain: str) -> str:
    if not plain:
        raise ValueError("密码不能为空")
    return _PH.hash(plain)


def verify_password(plain: str, stored: str) -> bool:
    """校验明文 vs 存盘值。

    仅接受 Argon2id 格式：旧 PBKDF2 哈希视为未设密码，由 bootstrap 重新生成。
    """
    if not stored or not isinstance(stored, str) or not stored.startswith("$argon2id$"):
        return False
    try:
        return _PH.verify(stored, plain)
    except (VerifyMismatchError, InvalidHashError, Argon2Error):
        return False


def random_password(length: int = 18) -> str:
    """生成临时密码：去歧义字符 + 4 组 4 位易记组合。

    拼接格式为「aaaa-bbbb-cccc-dddd」（4 组 4 位 + 3 个连字符，共 19 字符），
    默认 length=18 时截断为 18 位；但绝不能以连字符结尾——截断到连字符位置
    会留下「17 字母 + -」的难看尾巴。
    """
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    chunk = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))
    if len(chunk) <= length:
        return chunk
    # 截到 length 但若末位是 '-' 就再削一位
    out = chunk[:length]
    return out[:-1] if out.endswith("-") else out


def random_jwt_secret() -> str:
    """JWT HS256 签名密钥：32 字节随机十六进制（256 位熵）。"""
    return secrets.token_hex(32)
