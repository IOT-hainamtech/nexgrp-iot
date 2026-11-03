
import frappe


DATA_PLANE_ROLE = "IoT Data Plane"


def is_data_plane():
    """
    Verify the authenticated integration identity.

    No hardcoded email or device ID.
    """
    user = frappe.session.user

    if not user or user in ("Guest", "Administrator"):
        return False

    roles = frappe.get_roles(user)

    if DATA_PLANE_ROLE not in roles:
        return False

    # Fail closed if this integration user
    # is accidentally granted administrator rights.
    if "System Manager" in roles:
        return False

    if not frappe.db.get_value("User", user, "enabled"):
        return False

    request = getattr(frappe.local, "request", None)

    if request is None:
        return False

    authorization = request.headers.get(
        "Authorization", ""
    )

    # Accept token-authenticated API requests only.
    return authorization.lower().startswith("token ")


def require_data_plane():
    if not is_data_plane():
        frappe.throw(
            "IoT Data Plane API token required",
            frappe.PermissionError,
        )


def require_manager():
    user = frappe.session.user

    if user == "Administrator":
        return

    if (
        user not in (None, "Guest")
        and "System Manager" in frappe.get_roles(user)
    ):
        return

    frappe.throw(
        "System Manager permission required",
        frappe.PermissionError,
    )


def require_manager_or_data_plane():
    if is_data_plane():
        return

    require_manager()


def require_device_scope(device_id):
    """
    Data Plane may access only devices belonging
    to its assigned Organizations.
    """
    require_data_plane()

    if not isinstance(device_id, str) or not device_id.strip():
        frappe.throw(
            "device_id is required",
            frappe.ValidationError,
        )

    device_id = device_id.strip()

    site = frappe.db.get_value(
        "Device",
        device_id,
        "site",
    )

    if not site:
        frappe.throw(
            "Device not found or has no Site",
            frappe.PermissionError,
        )

    organization = frappe.db.get_value(
        "Site",
        site,
        "organization",
    )

    if not organization:
        frappe.throw(
            "Site has no Organization",
            frappe.PermissionError,
        )

    permission = frappe.db.exists(
        "User Permission",
        {
            "user": frappe.session.user,
            "allow": "Organization",
            "for_value": organization,
            "apply_to_all_doctypes": 1,
        },
    )

    if not permission:
        frappe.throw(
            "Device is outside permitted Organization",
            frappe.PermissionError,
        )

    return device_id


def require_command_scope(command_id):
    """
    Resolve the command's Device before allowing
    an ACK from Data Plane.
    """
    require_data_plane()

    if not isinstance(command_id, str) or not command_id.strip():
        frappe.throw(
            "command_id is required",
            frappe.ValidationError,
        )

    device_id = frappe.db.get_value(
        "Device Command",
        {"command_id": command_id.strip()},
        "device",
    )

    if not device_id:
        frappe.throw(
            "Command not found",
            frappe.PermissionError,
        )

    require_device_scope(device_id)

    return device_id
