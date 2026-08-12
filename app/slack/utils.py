from typing import Any


def escape_mrkdwn(value: object) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def state_value(state: dict[str, Any], block_id: str, action_id: str = "value") -> str | None:
    element = state.get("values", {}).get(block_id, {}).get(action_id, {})
    element_type = element.get("type", "")
    if element_type == "datepicker":
        return element.get("selected_date")
    if element_type.endswith("select"):
        selected = element.get("selected_option")
        return selected.get("value") if selected else None
    return element.get("value")


def input_element(
    action_id: str,
    *,
    initial_value: str | None = None,
    multiline: bool = False,
    placeholder: str | None = None,
) -> dict:
    element: dict = {
        "type": "plain_text_input",
        "action_id": action_id,
        "multiline": multiline,
    }
    if initial_value:
        element["initial_value"] = initial_value
    if placeholder:
        element["placeholder"] = {"type": "plain_text", "text": placeholder}
    return element
