def authorize_sender(
    *, sender_id: int, expected_operator_id: int, is_private: bool, is_forwarded: bool
) -> bool:
    return is_private and not is_forwarded and sender_id == expected_operator_id
