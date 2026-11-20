"""Route a failed Fiori session bind while preserving verified resume progress."""

import argparse
import json
from pathlib import Path


def route_session_failure(entry: str, session_result: Path, checkpoint: Path | None) -> dict:
    observation = json.loads(session_result.read_text(encoding="utf-8-sig"))
    if observation["status"] not in {"BLOCKED", "FAILURE", "NEED_DECISION"}:
        raise ValueError("Expected a failed session binding")

    result = {"session_failure_ref": str(session_result)}
    if entry == "RESUME_EXECUTION":
        if checkpoint is None:
            raise ValueError("A resume failure requires the original verified checkpoint")
        original = json.loads(checkpoint.read_text(encoding="utf-8-sig"))
        if not isinstance(original, dict) or not original.get("checkpoint_id"):
            raise ValueError("Invalid verified checkpoint")
        return {
            **result,
            "status": "PAUSED_FOR_INTERVENTION",
            "verified_recovery_checkpoint": original,
            "checkpoint_ref": str(checkpoint),
        }
    if entry not in {"NEW_CONVERSATION_REPORT", "CONTINUE_CONVERSATION"} or checkpoint is not None:
        raise ValueError("Only a fresh execution can create an initial session blocker")
    return {**result, "status": "INITIAL_SESSION_BLOCKER"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entry", choices=["NEW_CONVERSATION_REPORT", "CONTINUE_CONVERSATION", "RESUME_EXECUTION"])
    parser.add_argument("session_result", type=Path, help="Saved Evidence for this failed bind attempt")
    parser.add_argument("--checkpoint", type=Path, help="Verified checkpoint from the original run, for resume only")
    args = parser.parse_args()
    try:
        result = route_session_failure(args.entry, args.session_result, args.checkpoint)
    except (OSError, KeyError, ValueError) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
