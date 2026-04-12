# src/device_manager.py
from __future__ import annotations

import asyncio
import logging
import os
import sys

from src.models import DeviceInfo

logger = logging.getLogger("locmotion.device")

# pymobiledevice3 imports
from pymobiledevice3.usbmux import list_devices as _usbmux_list_devices
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
from pymobiledevice3.remote.utils import (
    stop_remoted_if_required, resume_remoted_if_required, get_rsds,
)
from pymobiledevice3.remote.tunnel_service import (
    create_core_device_tunnel_service_using_rsd,
    get_remote_pairing_tunnel_services,
    CoreDeviceTunnelProxy,
)
from pymobiledevice3.bonjour import DEFAULT_BONJOUR_TIMEOUT


def check_sudo():
    """Check if running as sudo on macOS. Required for pymobiledevice3."""
    if sys.platform == "darwin" and os.geteuid() != 0:
        logger.error("Not running as sudo. pymobiledevice3 requires root on macOS.")
        logger.error("Please run: sudo python -m src.main")
        return False
    return True


class DeviceManager:
    def __init__(self):
        self._connections: dict[str, dict] = {}
        self._dvt_session: _DVTSession | None = None
        self._tunnel_task: asyncio.Task | None = None
        self._tunnel_stop_event = asyncio.Event()

    async def list_devices(self) -> list[DeviceInfo]:
        """Discover connected iOS devices via USB."""
        devices: list[DeviceInfo] = []
        try:
            usb_devices = await _usbmux_list_devices()
            for dev in usb_devices:
                udid = dev.serial
                conn = dev.connection_type
                try:
                    lockdown = await create_using_usbmux(
                        serial=udid, connection_type=conn, autopair=True,
                    )
                    info = lockdown.short_info
                    devices.append(DeviceInfo(
                        udid=udid,
                        name=info.get("DeviceName", "Unknown"),
                        ios_version=info.get("ProductVersion", "Unknown"),
                        conn_type="USB" if conn == "USB" else "WiFi",
                    ))
                except Exception as e:
                    logger.warning(f"Could not query device {udid}: {e}")
        except Exception as e:
            logger.error(f"Error listing devices: {e}")
        return devices

    async def connect(self, udid: str, conn_type: str = "USB") -> bool:
        """Establish tunnel to device. Caches connection info."""
        if udid in self._connections:
            logger.info(f"Reusing existing connection for {udid}")
            return True

        try:
            lockdown = await create_using_usbmux(serial=udid, autopair=True)
            ios_version = lockdown.product_version
            major = int(ios_version.split(".")[0])

            if major >= 17:
                host, port = await self._start_tunnel_ios17(
                    udid, lockdown, ios_version,
                )
                self._connections[udid] = {
                    "host": host, "port": port,
                    "ios_version": ios_version,
                    "conn_type": conn_type,
                    "lockdown": None,
                }
            else:
                self._connections[udid] = {
                    "host": None, "port": None,
                    "ios_version": ios_version,
                    "conn_type": conn_type,
                    "lockdown": lockdown,
                }

            logger.info(
                f"Connected to {udid} (iOS {ios_version}) via {conn_type}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to connect to {udid}: {e}")
            return False

    async def disconnect(self, udid: str) -> bool:
        """Tear down connection and clean up DVT session."""
        await self._close_dvt()
        if udid in self._connections:
            del self._connections[udid]
        self._tunnel_stop_event.set()
        logger.info(f"Disconnected {udid}")
        return True

    async def set_location(self, lat: float, lng: float) -> bool:
        """Inject a single location update. Called at ~2 Hz by simulation."""
        if not self._connections:
            logger.error("No device connected")
            return False

        udid = next(iter(self._connections))
        conn = self._connections[udid]
        major = int(conn["ios_version"].split(".")[0])

        try:
            if self._dvt_session is None:
                self._dvt_session = await _DVTSession.open(conn, major)

            await self._dvt_session.set_location(lat, lng)
            return True

        except Exception as e:
            logger.error(f"Error setting location: {e}")
            await self._close_dvt()
            return False

    async def clear_location(self) -> bool:
        """Clear simulated location on device."""
        if not self._connections:
            return True

        udid = next(iter(self._connections))
        conn = self._connections[udid]
        major = int(conn["ios_version"].split(".")[0])

        try:
            if self._dvt_session is None:
                self._dvt_session = await _DVTSession.open(conn, major)
            await self._dvt_session.clear_location()
        except Exception as e:
            logger.error(f"Error clearing location: {e}")
        finally:
            await self._close_dvt()
        return True

    async def _close_dvt(self):
        if self._dvt_session:
            try:
                await self._dvt_session.close()
            except Exception:
                pass
            self._dvt_session = None

    async def _start_tunnel_ios17(
        self, udid: str, lockdown, ios_version: str,
    ) -> tuple[str, str]:
        """Start QUIC or TCP tunnel for iOS 17+ and return (host, port)."""
        parts = ios_version.split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0

        host = None
        port = None
        ready = asyncio.Event()

        if major == 17 and minor <= 3:
            stop_remoted_if_required()
            devices = await get_rsds(DEFAULT_BONJOUR_TIMEOUT)
            rsd = None
            for d in devices:
                if d.udid == udid:
                    rsd = d
                    break
            if rsd is None:
                raise RuntimeError(f"Could not find RSD for device {udid}")

            service = await create_core_device_tunnel_service_using_rsd(
                rsd, autopair=True,
            )

            async def _run_quic():
                nonlocal host, port
                async with service.start_quic_tunnel() as tunnel:
                    resume_remoted_if_required()
                    host = tunnel.address
                    port = str(tunnel.port)
                    ready.set()
                    await self._tunnel_stop_event.wait()

            self._tunnel_stop_event.clear()
            self._tunnel_task = asyncio.create_task(_run_quic())

        else:
            proxy = await CoreDeviceTunnelProxy.create(lockdown)

            async def _run_tcp():
                nonlocal host, port
                async with proxy.start_tcp_tunnel() as tunnel:
                    host = tunnel.address
                    port = str(tunnel.port)
                    ready.set()
                    await self._tunnel_stop_event.wait()

            self._tunnel_stop_event.clear()
            self._tunnel_task = asyncio.create_task(_run_tcp())

        await asyncio.wait_for(ready.wait(), timeout=30)
        return host, port


class _DVTSession:
    """Holds a persistent DVT connection for location injection."""

    def __init__(self, rsd_ctx, dvt_ctx, location_sim_ctx, location_sim):
        self._rsd_ctx = rsd_ctx
        self._dvt_ctx = dvt_ctx
        self._location_sim_ctx = location_sim_ctx
        self.location_sim = location_sim

    @classmethod
    async def open(cls, conn: dict, major_version: int) -> _DVTSession:
        if major_version >= 17:
            host = conn["host"]
            port = conn["port"]
            rsd = RemoteServiceDiscoveryService((host, port))
            await rsd.__aenter__()
            dvt = DvtProvider(rsd)
            await dvt.__aenter__()
            loc = LocationSimulation(dvt)
            await loc.__aenter__()
            return cls(rsd_ctx=rsd, dvt_ctx=dvt, location_sim_ctx=loc, location_sim=loc)
        else:
            lockdown = conn["lockdown"]
            dvt = DvtProvider(lockdown)
            await dvt.__aenter__()
            loc = LocationSimulation(dvt)
            await loc.__aenter__()
            return cls(rsd_ctx=None, dvt_ctx=dvt, location_sim_ctx=loc, location_sim=loc)

    async def set_location(self, lat: float, lng: float) -> None:
        await self.location_sim.set(lat, lng)

    async def clear_location(self) -> None:
        await self.location_sim.clear()

    async def close(self):
        try:
            await self._location_sim_ctx.__aexit__(None, None, None)
        except Exception:
            pass
        try:
            await self._dvt_ctx.__aexit__(None, None, None)
        except Exception:
            pass
        if self._rsd_ctx:
            try:
                await self._rsd_ctx.__aexit__(None, None, None)
            except Exception:
                pass
