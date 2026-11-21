"""Syntetyczne próbki C2PA: oryginał podpisany testowym certyfikatem (nie z listy zaufania), montaże, kopie bez manifestu."""
import datetime as dt
import io
import json

import pytest

pytest.importorskip("c2pa")
pytest.importorskip("cryptography")
PIL = pytest.importorskip("PIL.Image")

import c2pa  # noqa: E402
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID  # noqa: E402

from methadata.contracts import ids  # noqa: E402
from methadata.contracts.models import Artifact, ArtifactRole, InputKind, Observation  # noqa: E402


def _chain():
    ca_k, k = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    name = lambda cn: x509.Name([x509.NameAttribute(NameOID.COUNTRY_NAME, "PL"),  # noqa: E731
                                 x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Methadata Test"),
                                 x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    t0 = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    ca = (x509.CertificateBuilder().subject_name(name("Test CA")).issuer_name(name("Test CA"))
          .public_key(ca_k.public_key()).serial_number(1).not_valid_before(t0).not_valid_after(t0 + dt.timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_k.public_key()), False)
          .sign(ca_k, hashes.SHA256()))
    ee = (x509.CertificateBuilder().subject_name(name("Test Signer")).issuer_name(ca.subject)
          .public_key(k.public_key()).serial_number(2).not_valid_before(t0).not_valid_after(t0 + dt.timedelta(days=365))
          .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
          .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), True)
          .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_k.public_key()), False)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), False)
          .sign(ca_k, hashes.SHA256()))
    pem = ee.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM)
    key = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return pem, key


def _created(dst):
    return {"claim_generator_info": [{"name": "methadata-test", "version": "1"}],
            "assertions": [{"label": "c2pa.actions.v2", "data": {"actions": [
                {"action": "c2pa.created",
                 "digitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/" + dst}]}}]}


class Samples:
    def __init__(self):
        pem, key = _chain()
        self.signer = c2pa.Signer.from_info(c2pa.C2paSignerInfo(c2pa.C2paSigningAlg.ES256, pem, key, None))

    @staticmethod
    def image(color, fmt="JPEG"):
        b = io.BytesIO()
        PIL.new("RGB", (64, 64), color).save(b, fmt)
        return b.getvalue()

    def sign(self, data, title, dst="digitalCapture", ingredients=(), mime="image/jpeg"):
        b = c2pa.Builder(json.dumps(dict(_created(dst), title=title)))
        for ing_title, ing_bytes in ingredients:
            b.add_ingredient(json.dumps({"title": ing_title, "relationship": "componentOf"}), mime, io.BytesIO(ing_bytes))
        out = io.BytesIO()
        b.sign(self.signer, mime, io.BytesIO(data), out)
        return out.getvalue()


@pytest.fixture(scope="session")
def samples():
    return Samples()


class Mem:
    def __init__(self):
        self.d = {}

    def open_bytes(self, ref):
        return self.d[ref.artifact_id]


@pytest.fixture
def mem():
    return Mem()


@pytest.fixture
def make_obs(mem):
    def make(name, data, role=ArtifactRole.creative_image, media_type="image/jpeg"):
        oid = ids.observation_id("synthetic://" + name, "creative_only")
        sha = ids.sha256_bytes(data)
        a = Artifact(artifact_id=ids.artifact_id(oid, role.value, sha, name), observation_id=oid, role=role,
                     local_ref="x/" + name, sha256=sha, media_type=media_type, origin=name)
        mem.d[a.artifact_id] = data
        return Observation(observation_id=oid, canonical_id=ids.canonical_id([(role.value, sha)]),
                           source_ref="synthetic://" + name, input_kind=InputKind.creative_only, artifacts=[a],
                           tags=["synthetic"], capture_time="2026-11-01T10:00:00Z")
    return make
