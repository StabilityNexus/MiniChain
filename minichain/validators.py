import re


def is_valid_receiver(receiver):
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", receiver))
