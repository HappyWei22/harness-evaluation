"""Validate the zero-submission Windows isolation preflight before a formal permit."""

import ntpath
import re


# Win32 access denial, network path/name failures, and connection failures.
# File/path-not-found (2 and 3) and an unknown operating-system error are insufficient.
BLOCKED_HRESULTS = tuple(f"{0x80070000 | code:08X}" for code in (5, 53, 64, 65, 67, 121, 1225, 1231, 1232, 1236, 1326))


def require_matching_admission(request: dict, ready: dict, proof: dict, proof_sha256: str) -> None:
    context = proof.get("Context") or {}
    boot = context.get("BootEvent") or {}
    guest = proof.get("Guest") or {}
    files = context.get("ProtectedFiles") or []
    if (proof.get("Activity") != "interface_preflight" or proof.get("Verdict") != "pass"
            or proof.get("DshSubmissions") != 0
            or proof.get("ProbeId") != request.get("IsolationProbeId") or not proof.get("ProbeId")
            or proof.get("RequestId") != request["RequestId"] or context.get("VmId") != request["VmId"]
            or not boot.get("RecordId") or not boot.get("CreatedUtc") or boot != ready.get("BootEvent")
            or not re.fullmatch(r"[a-f0-9]{64}", proof_sha256)
            or ready.get("IsolationProofSha256") != proof_sha256
            or not re.fullmatch(r"[a-f0-9]{64}", proof.get("ContextSha256", ""))
            or ready.get("IsolationContextSha256") != proof["ContextSha256"]
            or guest.get("ProbeId") != proof["ProbeId"] or guest.get("BootEvent") != boot
            or guest.get("Profile") != request["ExpectedProfile"] or context.get("GuestProfile") != guest["Profile"]
            or not context.get("GuestSid") or guest.get("Sid") != context["GuestSid"]
            or type(guest.get("SessionId")) is not int or guest["SessionId"] <= 0
            or guest["SessionId"] not in proof.get("DesktopSessions", [])
            or any(source.get(key) != 0 for source in (context, guest)
                   for key in ("SmbMappings", "SmbConnections", "NetworkDisks"))
            or type(guest.get("SessionProtocol")) is not int or guest["SessionProtocol"] != 0):
        raise ValueError("The Windows isolation preflight does not match the active boot, user, and access configuration.")

    addresses = context.get("HostAddresses") or []
    checked = context.get("SmbEndpoints") or []
    if (context.get("HostIPv4") not in addresses
            or [(row.get("Address"), row.get("Port")) for row in checked]
            != [(address, port) for address in addresses for port in (139, 445)]
            or any(row.get("Reachable") is not False for row in checked)):
        raise ValueError("A host SMB endpoint is reachable or its check is missing.")

    expected_files = request["ProtectedFiles"]
    if len(files) != len(expected_files) or not files:
        raise ValueError("The isolation preflight did not cover the registered grading files.")
    paths = []
    for item, expected in zip(files, expected_files):
        drive, path = ntpath.splitdrive(expected["Path"])
        prefix = rf"\\{context['HostIPv4']}\{drive[0]}$"
        if ({key: item.get(key) for key in ("Role", "Path", "Sha256")} != expected
                or item.get("UNC") != prefix + path):
            raise ValueError("The checked share paths do not match the registered grading files.")
        paths.append(item["UNC"])
    checks = guest.get("ReadChecks") or []
    if [check.get("Path") for check in checks] != paths:
        raise ValueError("A Windows grading-file read check is missing or refers to another path.")
    if any(check.get("CanOpen") is not False or check.get("HResult") not in BLOCKED_HRESULTS for check in checks):
        raise ValueError("A grading file was readable or its Windows access error was inconclusive.")
