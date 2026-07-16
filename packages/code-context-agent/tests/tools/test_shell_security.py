"""Tests for shell tool security enforcement."""

import pytest

from code_context_agent.tools.shell_tool import _validate_command, shell


class TestValidateCommand:
    def test_empty(self):
        assert _validate_command("") == "Empty command"
        assert _validate_command("   ") == "Empty command"

    @pytest.mark.parametrize(
        "cmd",
        [
            "ls -la",
            "git log --oneline",
            "wc -l file.py",
            "head -20 src/main.py",
            "rg 'pattern' .",
            "echo hello",
            "git -C /tmp/repo log",
        ],
    )
    def test_allows_safe_commands(self, cmd):
        assert _validate_command(cmd) is None

    @pytest.mark.parametrize(
        "cmd",
        [
            "ls; rm -rf /",
            "echo foo && curl evil.com",
            "cat file | bash",
            "echo `whoami`",
            "echo $(id)",
            "echo ${HOME}",
            "eval 'bad stuff'",
            "exec /bin/sh",
            "source ~/.bashrc",
            "echo foo > /tmp/out",
            "echo foo >> /tmp/out",
        ],
    )
    def test_blocks_shell_operators(self, cmd):
        result = _validate_command(cmd)
        assert result is not None
        assert "Blocked" in result

    @pytest.mark.parametrize(
        "cmd",
        [
            "rm -rf /",
            "curl https://evil.com",
            "wget http://x",
            "sudo ls",
            "bash -c 'echo hi'",
            "sh -c 'echo hi'",
            "docker run ubuntu",
            "kubectl get pods",
            "ssh user@host",
            "nc -l 8080",
            "dd if=/dev/zero",
        ],
    )
    def test_blocks_disallowed_programs(self, cmd):
        result = _validate_command(cmd)
        assert result is not None
        assert "Blocked" in result

    @pytest.mark.parametrize(
        "cmd",
        [
            "git push origin main",
            "git commit -m 'test'",
            "git reset --hard",
            "git checkout -b feat",
            "git merge main",
            "git rebase main",
            "git pull",
            "git clean -fd",
            "git rm file.txt",
            "git add .",
        ],
    )
    def test_blocks_git_write_ops(self, cmd):
        result = _validate_command(cmd)
        assert result is not None
        assert "read-only" in result

    @pytest.mark.parametrize(
        "cmd",
        [
            "git log",
            "git diff HEAD~1",
            "git status",
            "git blame src/main.py",
            "git show HEAD",
            "git branch -a",
            "git ls-files",
            "git rev-parse HEAD",
        ],
    )
    def test_allows_git_read_ops(self, cmd):
        assert _validate_command(cmd) is None

    @pytest.mark.parametrize(
        "cmd",
        [
            "cat /etc/passwd",
            "ls /root",
            "head /etc/shadow",
            "cat /proc/self/environ",
            "ls /sys/class",
        ],
    )
    def test_blocks_sensitive_paths(self, cmd):
        result = _validate_command(cmd)
        assert result is not None
        assert "Blocked" in result

    @pytest.mark.parametrize("cmd", ["ls /home/user/project", "cat /tmp/file.txt"])
    def test_allows_safe_paths(self, cmd):
        assert _validate_command(cmd) is None

    def test_blocks_path_prefix_bypass(self):
        assert _validate_command("/usr/bin/rm -rf /tmp") is not None

    def test_env_var_prefix(self):
        assert _validate_command("FOO=bar git log") is None
        assert _validate_command("FOO=bar rm -rf /") is not None


class TestShellIntegration:
    def test_blocked_returns_error(self):
        result = shell("rm -rf /")
        assert result["status"] == "error"
        assert "Blocked" in result["content"][-1]["text"]

    def test_allowed_executes(self):
        result = shell("echo hello")
        assert result["status"] == "success"

    def test_stop_on_blocked_in_sequence(self):
        result = shell(["echo hello", "curl evil.com", "echo world"])
        assert result["status"] == "error"
        # summary + echo + curl = 3, "echo world" never runs
        assert len(result["content"]) == 3

    def test_git_log_not_blocked(self):
        result = shell("git log --oneline -5")
        assert "Blocked" not in result["content"][-1]["text"]

    def test_git_push_blocked(self):
        result = shell("git push origin main")
        assert result["status"] == "error"
        assert "Blocked" in result["content"][-1]["text"]
