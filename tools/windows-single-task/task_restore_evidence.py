"""Review a recorded final restore rejected by the old record-number comparison."""

from datetime import datetime, timezone


REUSED_RECORD_ERROR = "The restored guest reports the startup event that preceded the restore."


def verify_reused_record_restore(request: dict, stage: dict, first: dict, final: dict, attempt_id: str) -> None:
    before, after = first["Guest"], final["Guest"]
    boot_before, boot_after = before["BootEvent"], after["BootEvent"]
    restore = stage["FinalRestore"]
    cleanup = stage["Cleanup"]
    residue = after["RunResidue"]
    if (first.get("Verdict") != "pass" or final.get("Verdict") != "fail" or final.get("Stage") != "verify_guest"
            or final.get("Error") != REUSED_RECORD_ERROR or restore.get("Error") != REUSED_RECORD_ERROR
            or final.get("RunId") != attempt_id or stage.get("RequestId") != request["RequestId"]
            or any(item.get(key) != request[key] for key in ("VmId", "CheckpointId", "CheckpointName")
                   for item in (first, final))
            or final.get("InitialVmState") != "Off" or not final.get("Heartbeat", "").startswith("Ok")
            or any(guest.get("ProfileMatches") is not True or guest.get("Profile") != request["ExpectedProfile"]
                   for guest in (before, after))
            or stage.get("BootEvent") != boot_before or restore.get("BootEvent") != boot_after
            or not boot_after.get("RecordId") or boot_after["RecordId"] != boot_before["RecordId"]
            or after.get("DshProcesses") != [] or after.get("HarnessTasks") != []
            or restore.get("Residue") != residue
            or any(residue.get(key) is not False for key in ("RunWorkspaceExists", "RunLauncherExists", "RunSessionExists"))
            or residue.get("PreflightTaskRemains") != 0 or stage.get("CleanupError")
            or cleanup.get("TaskRemains") != 0 or cleanup.get("DshRemains") != 0
            or cleanup.get("WorkingRootExists") is not False):
        raise ValueError("The archived reports do not establish a verified v2 restoration and cleanup.")

    started, applied, vm_started, finished = (
        datetime.fromisoformat(final[key]) for key in ("StartedAt", "RestoreAppliedAt", "StartReturnedAt", "FinishedAt"))
    before_event = datetime.fromisoformat(boot_before["CreatedUtc"])
    after_event = datetime.fromisoformat(boot_after["CreatedUtc"])
    before_boot = datetime.fromisoformat(before["BootTime"])
    after_boot = datetime.fromisoformat(after["BootTime"])
    native_end = datetime.fromtimestamp(stage["Completion"]["NativeTurnEnd"]["time"] / 1000, tz=timezone.utc)
    if (not started <= applied <= vm_started <= finished or not applied <= after_boot <= finished
            or not before_event < native_end < after_event <= finished or not before_boot < after_boot):
        raise ValueError("The native turn and Windows startup times do not establish a new boot for this restore.")
