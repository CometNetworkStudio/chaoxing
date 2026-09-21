# -*- coding: utf-8 -*-
"""OmniTask 接入适配器（由 CometNetworkStudio 添加）。

把 OmniTask 的任务映射到本项目的 CLI（main.py）：
- 从 stdin 读取 OmniTask 的 execute 请求（stdio JSON 协议，见 OmniTask docs/script-protocol.md）；
- 生成临时 config.ini（含账号/课程/参数；权限 0600，用后删除，避免凭据出现在命令行）；
- 以子进程运行 ``python main.py -c <临时配置>``，合并 stderr（本项目日志/进度走 stderr），
  逐行解析 ``NN%`` 作为进度上报；
- 退出码非 0 视为失败。

用法（被 OmniTask Host 以子进程调用）：``python omnitask_entry.py``。
本文件依赖 OmniTask SDK（``omnitask_sdk``）。
"""

import configparser
import io
import os
import re
import subprocess
import sys
import tempfile

from omnitask_sdk import action, listing, serve

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRESS_RE = re.compile(r"(\d{1,3})%")


def _config_text(params: dict, credentials: dict) -> str:
    parser = configparser.ConfigParser()
    # 课程列表为空时传 "all"：本项目 filter_courses 匹配不到会回退为全部课程，且避免交互式 input()。
    course_list = (params.get("course_list") or "").strip() or "all"
    parser["common"] = {
        "username": credentials.get("username", ""),
        "password": credentials.get("password", ""),
        "course_list": course_list,
        "speed": str(params.get("speed", "1")),
        "jobs": str(params.get("jobs", "4")),
        "notopen_action": params.get("notopen_action", "retry"),
    }
    if params.get("tiku_provider"):
        parser["tiku"] = {
            "provider": params["tiku_provider"],
            "token": params.get("tiku_token", ""),
            "submit": params.get("tiku_submit", "false"),
        }
    if params.get("notification_provider") and params.get("notification_url"):
        parser["notification"] = {
            "provider": params["notification_provider"],
            "url": params["notification_url"],
        }
    buf = io.StringIO()
    parser.write(buf)
    return buf.getvalue()


def _run(ctx, extra_args: list) -> None:
    fd, config_path = tempfile.mkstemp(prefix="chaoxing-", suffix=".ini")
    os.close(fd)
    try:
        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write(_config_text(ctx.params, ctx.credentials))
        os.chmod(config_path, 0o600)

        argv = [sys.executable, os.path.join(HERE, "main.py"), "-c", config_path, *extra_args]
        proc = subprocess.Popen(
            argv,
            cwd=HERE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # 本项目日志/进度输出到 stderr，这里合并
            text=True,
            bufsize=1,
        )
        percent = 0
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.strip()
            match = PROGRESS_RE.search(line)
            if match:
                percent = min(100, int(match.group(1)))
            if line:
                ctx.progress(percent, line[:200])
        code = proc.wait()
        if code != 0:
            raise RuntimeError(f"chaoxing 以退出码 {code} 结束")
    finally:
        try:
            os.remove(config_path)
        except OSError:
            pass


@action("study")
def study(ctx) -> None:
    """刷课：完成课程任务点（视频/文档/章节检测/阅读/直播）。"""
    _run(ctx, [])


@action("learning_count")
def learning_count(ctx) -> None:
    """增加章节学习次数（-lc；目标次数 -tc）。"""
    target = str(ctx.params.get("target_count", "100"))
    _run(ctx, ["-lc", "-tc", target])


@listing()
def courses(ctx):
    """发现：列出该账号的课程（供 OmniTask 选择）。"""
    from api.base import Account, Chaoxing

    username = ctx.credentials.get("username", "")
    password = ctx.credentials.get("password", "")
    if not username or not password:
        raise RuntimeError("缺少账号密码凭据")
    chaoxing = Chaoxing(account=Account(username, password))
    state = chaoxing.login()
    if not state.get("status"):
        raise RuntimeError(state.get("msg", "登录失败"))
    items = []
    for course in chaoxing.get_course_list():
        cid = str(course.get("courseId") or "")
        items.append(
            {
                "key": cid,
                "fields": {
                    "course_id": cid,
                    "clazz_id": str(course.get("clazzId") or ""),
                    "title": str(course.get("title") or ""),
                },
            }
        )
    return items


if __name__ == "__main__":
    serve()
