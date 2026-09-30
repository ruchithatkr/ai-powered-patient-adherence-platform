import pytest
from agents.supervisor import Supervisor


@pytest.fixture
def sup():
    return Supervisor()


def run(sup, ref, msg="", role="care_coordinator", ident=None):
    return sup.start(patient_ref=ref, purpose="adherence_support", requester="coord-1", requester_role=role,
                     identity_patient_ref=ident or ref, patient_message=msg)
