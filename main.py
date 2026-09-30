from __future__ import annotations

import os
import sys
import time
from datetime import datetime

import oci
from dotenv import load_dotenv


CAPACITY_ERROR_MARKERS = (
    "out of capacity",
    "out of host capacity",
    "capacity is not available",
)


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and (value is None or not value.strip()):
        raise ValueError(f"Missing required environment variable: {name}")
    return "" if value is None else value.strip()


def env_bool(name: str, default: bool) -> bool:
    raw = env(name, "true" if default else "false").lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be true/false")


def log(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def load_settings() -> dict:
    load_dotenv()

    settings = {
        "tenancy": env("OCI_TENANCY_OCID", required=True),
        "user": env("OCI_USER_OCID", required=True),
        "fingerprint": env("OCI_FINGERPRINT", required=True),
        "key_file": os.path.expandvars(os.path.expanduser(env("OCI_PRIVATE_KEY_PATH", required=True))),
        "pass_phrase": env("OCI_PRIVATE_KEY_PASSPHRASE") or None,
        "region": env("OCI_REGION", "ap-tokyo-1"),
        "compartment_id": env("OCI_COMPARTMENT_OCID", required=True),
        "subnet_id": env("OCI_SUBNET_OCID", required=True),
        "image_id": env("OCI_IMAGE_OCID", required=True),
        "availability_domain": env("OCI_AVAILABILITY_DOMAIN") or None,
        "ssh_public_key_path": os.path.expandvars(
            os.path.expanduser(env("SSH_PUBLIC_KEY_PATH", required=True))
        ),
        "display_name": env("INSTANCE_DISPLAY_NAME", "GMT-Server"),
        "shape": env("INSTANCE_SHAPE", "VM.Standard.A1.Flex"),
        "ocpus": float(env("INSTANCE_OCPUS", "4")),
        "memory_gb": float(env("INSTANCE_MEMORY_GB", "24")),
        "retry_seconds": int(env("RETRY_SECONDS", "600")),
        "assign_public_ip": env_bool("ASSIGN_PUBLIC_IP", True),
    }

    if settings["retry_seconds"] < 60:
        raise ValueError("RETRY_SECONDS must be at least 60")

    if not os.path.isfile(settings["key_file"]):
        raise FileNotFoundError(f"OCI private key not found: {settings['key_file']}")

    if not os.path.isfile(settings["ssh_public_key_path"]):
        raise FileNotFoundError(
            f"SSH public key not found: {settings['ssh_public_key_path']}"
        )

    return settings


def build_oci_config(settings: dict) -> dict:
    config = {
        "tenancy": settings["tenancy"],
        "user": settings["user"],
        "fingerprint": settings["fingerprint"],
        "key_file": settings["key_file"],
        "region": settings["region"],
    }
    if settings["pass_phrase"]:
        config["pass_phrase"] = settings["pass_phrase"]

    oci.config.validate_config(config)
    return config


def get_availability_domain(identity_client, settings: dict) -> str:
    if settings["availability_domain"]:
        return settings["availability_domain"]

    ads = identity_client.list_availability_domains(
        settings["compartment_id"]
    ).data
    if not ads:
        raise RuntimeError("No availability domain found")

    return ads[0].name


def read_ssh_public_key(path: str) -> str:
    with open(path, "r", encoding="utf-8") as file:
        key = file.read().strip()
    if not key:
        raise ValueError(f"SSH public key is empty: {path}")
    return key


def find_existing_instance(compute_client, settings: dict):
    response = compute_client.list_instances(
        compartment_id=settings["compartment_id"],
        display_name=settings["display_name"],
    )

    active_states = {
        "MOVING",
        "PROVISIONING",
        "STARTING",
        "RUNNING",
        "STOPPING",
        "STOPPED",
    }

    for instance in response.data:
        if instance.lifecycle_state in active_states:
            return instance

    return None


def get_public_ip(compute_client, network_client, settings: dict, instance_id: str):
    attachments = compute_client.list_vnic_attachments(
        compartment_id=settings["compartment_id"],
        instance_id=instance_id,
    ).data

    for attachment in attachments:
        if attachment.lifecycle_state == "ATTACHED":
            vnic = network_client.get_vnic(attachment.vnic_id).data
            if vnic.public_ip:
                return vnic.public_ip

    return None


def is_capacity_error(exc: oci.exceptions.ServiceError) -> bool:
    text = f"{exc.code} {exc.message}".lower()
    return any(marker in text for marker in CAPACITY_ERROR_MARKERS)


def launch_instance(compute_client, settings: dict, availability_domain: str, ssh_key: str):
    details = oci.core.models.LaunchInstanceDetails(
        availability_domain=availability_domain,
        compartment_id=settings["compartment_id"],
        display_name=settings["display_name"],
        shape=settings["shape"],
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=settings["ocpus"],
            memory_in_gbs=settings["memory_gb"],
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=settings["subnet_id"],
            assign_public_ip=settings["assign_public_ip"],
            display_name=f"{settings['display_name']}-vnic",
        ),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            image_id=settings["image_id"],
            source_type="image",
        ),
        metadata={"ssh_authorized_keys": ssh_key},
        is_pv_encryption_in_transit_enabled=True,
    )
    return compute_client.launch_instance(details).data


def wait_until_running(compute_client, instance):
    response = oci.wait_until(
        compute_client,
        compute_client.get_instance(instance.id),
        "lifecycle_state",
        "RUNNING",
        max_wait_seconds=1800,
        succeed_on_not_found=False,
    )
    return response.data


def main() -> int:
    try:
        settings = load_settings()
        config = build_oci_config(settings)

        identity_client = oci.identity.IdentityClient(config)
        compute_client = oci.core.ComputeClient(config)
        network_client = oci.core.VirtualNetworkClient(config)

        availability_domain = get_availability_domain(identity_client, settings)
        ssh_key = read_ssh_public_key(settings["ssh_public_key_path"])

        log(
            f"Target: {settings['shape']} / "
            f"{settings['ocpus']:g} OCPU / {settings['memory_gb']:g} GB / "
            f"{settings['region']} / {availability_domain}"
        )

        existing = find_existing_instance(compute_client, settings)
        if existing:
            log(
                f"Existing instance found: {existing.display_name} "
                f"({existing.lifecycle_state}) / {existing.id}"
            )
            if existing.lifecycle_state == "RUNNING":
                public_ip = get_public_ip(
                    compute_client, network_client, settings, existing.id
                )
                log(f"Public IP: {public_ip or 'not assigned yet'}")
            return 0

        attempt = 0
        while True:
            attempt += 1
            log(f"Launch attempt #{attempt}")

            try:
                instance = launch_instance(
                    compute_client, settings, availability_domain, ssh_key
                )
            except oci.exceptions.ServiceError as exc:
                if is_capacity_error(exc):
                    log(
                        f"Capacity unavailable. Retrying in "
                        f"{settings['retry_seconds']} seconds."
                    )
                    time.sleep(settings["retry_seconds"])
                    continue

                log(
                    f"OCI API error: status={exc.status}, "
                    f"code={exc.code}, message={exc.message}"
                )
                return 1

            log(f"Launch accepted: {instance.id}")
            running = wait_until_running(compute_client, instance)
            public_ip = get_public_ip(
                compute_client, network_client, settings, running.id
            )

            log("SUCCESS - instance is RUNNING")
            log(f"Instance OCID: {running.id}")
            log(f"Public IP: {public_ip or 'not assigned yet'}")
            return 0

    except KeyboardInterrupt:
        log("Stopped by user")
        return 130
    except Exception as exc:
        log(f"Fatal error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
