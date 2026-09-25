"""Hard upper bound for any future model adapter; current solver uses no LLM."""

MAX_TOTAL_PARAMETERS = 10_000_000_000


def require_allowed_model(name: str, total_parameters: int | None) -> None:
    if (
        not name
        or isinstance(total_parameters, bool)
        or not isinstance(total_parameters, int)
        or not 0 <= total_parameters <= MAX_TOTAL_PARAMETERS
    ):
        raise ValueError("Model must have a verified total parameter count of at most 10B")


require_allowed_model("deterministic-policy-engine", 0)
