"""Shared Tauc display choices, including the pre-2.1 preference alias."""

TAUC_DIRECT = "Tauc 直接（2）"
TAUC_INDIRECT = "Tauc 间接（1/2）"
TAUC_OPTIONS = (TAUC_DIRECT, TAUC_INDIRECT)


def normalize_tauc_mode(mode: str) -> str:
    return TAUC_DIRECT if mode == "Tauc 计算结果" else mode


def tauc_exponent(mode: str):
    mode = normalize_tauc_mode(mode)
    return 2.0 if mode == TAUC_DIRECT else 0.5 if mode == TAUC_INDIRECT else None


def tauc_label(exponent: float) -> str:
    return "Tauc (A·eV)²" if exponent == 2.0 else "Tauc (A·eV)^(1/2)"
