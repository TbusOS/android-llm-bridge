"""MCP tools: alb_app_install / uninstall / start / stop / list / info / clear_data."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alb.capabilities.app import (
    clear_data,
    info,
    install,
    list_apps,
    start,
    stop,
    uninstall,
)
from alb.mcp.transport_factory import build_transport


def register(mcp) -> None:  # noqa: ANN001
    @mcp.tool()
    async def alb_app_install(
        apk_path: str,
        replace: bool = True,
        grant_runtime: bool = False,
        downgrade: bool = False,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Install an APK.

        Args:
            apk_path: local path on the host (not on device)
            replace: -r (overwrite existing)
            grant_runtime: -g (auto-grant runtime permissions)
            downgrade: -d (allow version downgrade)
        """
        t = build_transport(device_serial=device)
        r = await install(
            t, Path(apk_path),
            replace=replace,
            grant_runtime=grant_runtime,
            downgrade=downgrade,
        )
        return r.to_dict()

    @mcp.tool()
    async def alb_app_uninstall(
        package: str,
        keep_data: bool = False,
        allow_dangerous: bool = False,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Uninstall a package. ASK-level permission (user data may be lost)."""
        t = build_transport(device_serial=device)
        r = await uninstall(
            t, package,
            keep_data=keep_data,
            allow_dangerous=allow_dangerous,
        )
        return r.to_dict()

    @mcp.tool()
    async def alb_app_start(
        component: str, device: str | None = None
    ) -> dict[str, Any]:
        """Start an app or activity.

        Args:
            component: "com.example" (default launcher activity) or
                       "com.example/.MainActivity"
        """
        t = build_transport(device_serial=device)
        r = await start(t, component)
        return r.to_dict()

    @mcp.tool()
    async def alb_app_stop(
        package: str, device: str | None = None
    ) -> dict[str, Any]:
        """Force-stop a package."""
        t = build_transport(device_serial=device)
        r = await stop(t, package)
        return r.to_dict()

    @mcp.tool()
    async def alb_app_list(
        filter: str | None = None,  # noqa: A002
        include_system: bool = False,
        device: str | None = None,
    ) -> dict[str, Any]:
        """List installed packages.

        Args:
            filter: substring to match against package names
            include_system: include /system apps
        """
        t = build_transport(device_serial=device)
        r = await list_apps(t, filter=filter, include_system=include_system)
        return r.to_dict()

    @mcp.tool()
    async def alb_app_info(
        package: str, device: str | None = None
    ) -> dict[str, Any]:
        """Return versionName/Code, install/update time, requested permissions."""
        t = build_transport(device_serial=device)
        r = await info(t, package)
        return r.to_dict()

    @mcp.tool()
    async def alb_app_clear_data(
        package: str,
        allow_dangerous: bool = False,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Clear app data. DESTRUCTIVE — ASK-level permission."""
        t = build_transport(device_serial=device)
        r = await clear_data(t, package, allow_dangerous=allow_dangerous)
        return r.to_dict()
