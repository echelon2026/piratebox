import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))

from piratebox.commands import CommandRegistry  # noqa: E402
from piratebox.engine import CommandEngine  # noqa: E402
from piratebox.fl.controller import FLController  # noqa: E402
from piratebox.fl.link import FLLink  # noqa: E402
from piratebox.fl.simulator import SimulatedPortPair  # noqa: E402
from piratebox.interpreters import RuleInterpreter  # noqa: E402
from piratebox.journal import Journal  # noqa: E402


@pytest.fixture
def registry():
    return CommandRegistry()


@pytest.fixture
def sim():
    return SimulatedPortPair()


@pytest.fixture
def engine(sim, registry, tmp_path):
    link = FLLink(sim, timeout=0.5)
    yield CommandEngine(FLController(link), registry, RuleInterpreter(), Journal(tmp_path))
    link.close()
