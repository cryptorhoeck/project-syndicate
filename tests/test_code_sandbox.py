"""Tests for Phase 8C Tier 1 — Code Sandbox."""

__version__ = "0.1.0"

import pytest
from src.sandbox.security import scan_script, hash_script, try_compile


class TestSandboxSecurity:

    def test_blocks_os_import(self):
        safe, err = scan_script("import os\nos.system('rm -rf /')")
        assert not safe
        assert "os" in err

    def test_blocks_subprocess(self):
        safe, err = scan_script("import subprocess")
        assert not safe

    def test_blocks_open(self):
        safe, err = scan_script("f = open('secrets.txt')")
        assert not safe
        assert "open" in err.lower()

    def test_blocks_eval(self):
        safe, err = scan_script("eval('1+1')")
        assert not safe

    def test_blocks_dunder_access(self):
        safe, err = scan_script("x = obj.__class__")
        assert not safe
        assert "dunder" in err.lower()

    def test_allows_math_import(self):
        safe, err = scan_script("import math\nx = math.sqrt(4)")
        assert safe
        assert err is None

    def test_allows_numpy_import(self):
        safe, err = scan_script("import numpy as np\nx = np.array([1,2,3])")
        assert safe

    def test_allows_pandas_import(self):
        safe, err = scan_script("import pandas as pd")
        assert safe

    def test_max_script_length(self):
        safe, err = scan_script("x = 1\n" * 3000)
        assert not safe
        assert "too long" in err.lower()

    def test_syntax_error_caught(self):
        ok, err = try_compile("def foo(:\n  pass")
        assert not ok
        assert err is not None

    def test_valid_script_compiles(self):
        ok, err = try_compile("x = 2 + 2")
        assert ok

    def test_script_hash_deterministic(self):
        h1 = hash_script("x = 1")
        h2 = hash_script("x = 1")
        assert h1 == h2

    def test_different_scripts_different_hash(self):
        h1 = hash_script("x = 1")
        h2 = hash_script("x = 2")
        assert h1 != h2


class TestSandboxExecution:

    @pytest.mark.asyncio
    async def test_simple_math(self):
        from src.sandbox.runner import execute_script
        from src.sandbox.data_api import SandboxDataAPI

        api = SandboxDataAPI(1)
        result = await execute_script("output(2 + 2)", api, 1)
        assert result.success
        assert result.output == 4

    @pytest.mark.asyncio
    async def test_blocked_script_fails(self):
        from src.sandbox.runner import execute_script

        result = await execute_script("import os", None, 1)
        assert not result.success
        assert "blocked" in result.error.lower()

    @pytest.mark.asyncio
    async def test_output_function(self):
        from src.sandbox.runner import execute_script
        from src.sandbox.data_api import SandboxDataAPI

        api = SandboxDataAPI(1)
        result = await execute_script(
            "data = {'mean': 42, 'count': 10}\noutput(data)",
            api, 1,
        )
        assert result.success
        assert result.output["mean"] == 42

    @pytest.mark.asyncio
    async def test_numpy_works(self):
        from src.sandbox.runner import execute_script
        from src.sandbox.data_api import SandboxDataAPI

        api = SandboxDataAPI(1)
        result = await execute_script(
            "import numpy as np\noutput(float(np.mean([1,2,3,4,5])))",
            api, 1,
        )
        assert result.success
        assert result.output == 3.0

    @pytest.mark.asyncio
    async def test_cost_calculation(self):
        from src.sandbox.runner import execute_script
        from src.sandbox.data_api import SandboxDataAPI

        api = SandboxDataAPI(1)
        result = await execute_script("output(1)", api, 1)
        assert result.cost_usd > 0

    @pytest.mark.asyncio
    async def test_data_api_functions_available(self):
        from src.sandbox.runner import execute_script
        from src.sandbox.data_api import SandboxDataAPI

        api = SandboxDataAPI(1, ["BTC/USDT"])
        result = await execute_script(
            "trades = get_my_trades()\noutput(len(trades))",
            api, 1,
        )
        assert result.success
        assert result.output == 0  # empty cache

    @pytest.mark.asyncio
    async def test_runtime_error_caught(self):
        from src.sandbox.runner import execute_script
        from src.sandbox.data_api import SandboxDataAPI

        api = SandboxDataAPI(1)
        result = await execute_script("x = 1 / 0", api, 1)
        assert not result.success
        assert "ZeroDivision" in result.error


class TestToolActions:

    def test_execute_analysis_in_roles(self):
        from src.agents.roles import get_role
        for role_name in ["scout", "strategist", "critic", "operator"]:
            role = get_role(role_name)
            assert "execute_analysis" in role.available_actions
            assert "run_tool" in role.available_actions
            assert "modify_genome" in role.available_actions
