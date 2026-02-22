import hashlib


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()
