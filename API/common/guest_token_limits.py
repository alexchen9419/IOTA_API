"""guest_tokens 的共用約定（generate_guest_qr / guest_access / list_guest_tokens / revoke_guest_token）。"""

# guest_tokens.max_uses 是 NOT NULL；「不限次數」存這個值，對外一律顯示成 null。
UNLIMITED_USES = 2147483647


def remaining_uses(max_uses, used_count):
    """剩餘次數；不限次數回 None。舊資料 max_uses<=0 也當不限。"""
    max_uses = int(max_uses or 0)
    if max_uses <= 0 or max_uses >= UNLIMITED_USES:
        return None
    return max(max_uses - int(used_count or 0), 0)
