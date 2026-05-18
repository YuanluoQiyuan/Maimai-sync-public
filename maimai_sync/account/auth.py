"""密码哈希与验证

使用 hashlib + salt 实现，不引入额外依赖（bcrypt 需要 C 编译）。
安全性足够用于本地工具场景。
"""

import hashlib
import os
import base64


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """哈希密码

    Args:
        password: 明文密码
        salt: 盐值，None 时自动生成

    Returns:
        (hash_hex, salt_b64) 哈希值和 Base64 编码的盐
    """
    if salt is None:
        salt = os.urandom(32)
    # PBKDF2 + SHA-256，100000 次迭代
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return dk.hex(), base64.b64encode(salt).decode("ascii")


def hash_password(password: str) -> str:
    """哈希密码，返回可存储的字符串

    格式: pbkdf2_sha256$iterations$salt_b64$hash_hex
    """
    hash_hex, salt_b64 = _hash_password(password)
    return f"pbkdf2_sha256$100000${salt_b64}${hash_hex}"


def verify_password(password: str, stored_hash: str) -> bool:
    """验证密码

    Args:
        password: 待验证的明文密码
        stored_hash: hash_password() 生成的哈希字符串

    Returns:
        密码是否正确
    """
    try:
        parts = stored_hash.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        iterations = int(parts[1])
        salt = base64.b64decode(parts[2])
        expected_hash = parts[3]
        # 用同样的 salt 和迭代次数重新哈希
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return dk.hex() == expected_hash
    except Exception:
        return False
