import asyncio
import platform
import subprocess
import threading
import time
import traceback

from pathlib import Path

import psutil
from logzero import logger
import pynvml
from monitor import Monitor, print_json

try:
    pynvml.nvmlInit()
except:
    traceback.print_exc()
    logger.info("本设备gpu获取不适配")
from PIL import ImageGrab


class WinFps(object):
    frame_que = list()
    single_instance = None
    fps_process = None

    def __init__(self, pid):
        self.pid = pid

    def __new__(cls, *args, **kwargs):
        if not cls.single_instance:
            cls.single_instance = super().__new__(cls)
        return cls.single_instance

    def fps(self):
        if not WinFps.fps_process:
            threading.Thread(target=self.start_fps_collect, args=(self.pid,)).start()
        if self.check_queue_head_frames_complete():
            return self.pop_complete_fps()
        else:
            return []

    @staticmethod
    def check_queue_head_frames_complete():
        if not WinFps.frame_que:
            return False
        head_time = int(WinFps.frame_que[0])
        end_time = int(WinFps.frame_que[-1])
        if head_time == end_time:
            return False
        return True

    @staticmethod
    def pop_complete_fps():
        head_time = int(WinFps.frame_que[0])
        complete_fps = []
        while int(WinFps.frame_que[0]) == head_time:
            complete_fps.append(WinFps.frame_que.pop(0))
        return complete_fps

    def start_fps_collect(self, pid):
        res_terminate = subprocess.Popen(
            ["PresentMon.exe", "-process_id", pid, "-output_stdout", "-stop_existing_session"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        WinFps.fps_process = res_terminate
        res_terminate.stdout.readline()
        while not res_terminate.poll():
            line = res_terminate.stdout.readline()
            if not line:
                try:
                    res_terminate.kill()
                except:
                    traceback.print_exc()
                break
            try:
                line = line.decode(encoding="utf-8")
                line_list = line.split(",")
                logger.info(line_list)
            except:
                time.sleep(1)
                traceback.print_exc()


async def sys_info():
    def real_func():
        current_platform = platform.system()
        computer_name = platform.node()
        res = {"platform": current_platform, "computer_name": computer_name, "time": time.time(),
               "cpu_cores": psutil.cpu_count(), "ram": "{0}G".format(int(psutil.virtual_memory().total / 1024 ** 3)),
               "rom": "{0}G".format(int(psutil.disk_usage('/').total / 1024 ** 3))}
        print_json(res)
        return res

    return await asyncio.run(asyncio.to_thread(real_func))


async def pids():
    def real_func():
        process_list = []
        for proc in psutil.process_iter(attrs=['name', 'pid', 'cmdline', 'username']):
            try:
                if ("SYSTEM" not in str(proc.username())) and ("root" not in str(proc.username())):
                    process_list.append(
                        {"name": proc.info['name'], "pid": proc.info['pid'], "cmd": proc.info['cmdline'],
                         "username": proc.username()})
            except Exception as e:
                logger.error(e)
        print_json(process_list)
        return process_list

    return await asyncio.run(asyncio.to_thread(real_func))


async def screenshot(pid, save_dir):
    def real_func(pid, save_dir):
        start_time = int(time.time())
        if pid:
            window = None
            if platform.system() == "Windows":
                import ctypes
                import pygetwindow as gw
                def get_pid(hwnd):
                    pid = ctypes.wintypes.DWORD()
                    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    return pid.value

                def get_window_by_pid(pid):
                    for window in gw.getAllWindows():
                        if get_pid(window._hWnd) == pid:
                            return window
                    return None

                window = get_window_by_pid(int(pid))
            dir_instance = Path(save_dir)
            screenshot_dir = dir_instance.joinpath("screenshot")
            screenshot_dir.mkdir(exist_ok=True)
            if window:
                screenshot = ImageGrab.grab(
                    bbox=(window.left, window.top, window.left + window.width, window.top + window.height),
                    all_screens=True)
            else:
                screenshot = ImageGrab.grab(all_screens=True)
            screenshot.save(screenshot_dir.joinpath(str(start_time) + ".png"), format="PNG")

    return await asyncio.run(asyncio.to_thread(real_func, (pid, save_dir)))


async def cpu(pid):
    def real_func(pid):
        start_time = int(time.time())
        proc = psutil.Process(pid=int(pid))
        cpu_time = proc.cpu_times()
        cpu_usage = proc.cpu_percent(interval=1)
        cpu_count = psutil.cpu_count()
        res = {"cpu_usage": cpu_usage / cpu_count, "cpu_usage_all": cpu_usage, "cpu_time": cpu_time,
               "cpu_core_num": cpu_count, "time": start_time}
        print_json(res)
        return res

    return await asyncio.run(asyncio.to_thread(real_func, (pid,)))


async def memory(pid):
    def real_func(pid):
        start_time = int(time.time())
        process = psutil.Process(int(pid))
        process_memory_info = process.memory_info()
        process_memory_usage = process_memory_info.rss / (1024 ** 2)  # In MB
        total_memory = psutil.virtual_memory().total / (1024 ** 2)  # In MB
        percentage_used = round((process_memory_usage / total_memory) * 100, 4)
        total_memory = total_memory / 1024
        memory_info = {"process_memory_info": process_memory_usage, "total_memory": total_memory,
                       "percentage_used": percentage_used, "time": start_time}
        print_json(memory_info)
        return memory_info

    return await asyncio.run(asyncio.to_thread(real_func, (pid,)))


async def fps(pid):
    frames = await WinFps(pid).fps()
    res = {"type": "fps", "fps": len(frames), "frames": frames, "time": int(frames[0])}
    print_json(res)
    return res


async def gpu(pid):
    def real_func(pid):
        pid = int(pid)
        start_time = int(time.time())
        device_count = pynvml.nvmlDeviceGetCount()
        if device_count == 1:
            deviceHandle = pynvml.nvmlDeviceGetHandleByIndex(0)  # 获取第一块GPU的句柄
            gpuUtilization = pynvml.nvmlDeviceGetUtilizationRates(deviceHandle)
            gpu_utilization_percentage = gpuUtilization.gpu  # GPU的计算使用率
            res = {"gpu_used": gpu_utilization_percentage, "time": start_time}
        for i in range(device_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            processes = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
            for process in processes:
                print(process)
                if process.pid == pid:
                    gpuUtilization = pynvml.nvmlDeviceGetUtilizationRates(deviceHandle)
                    gpu_utilization_percentage = gpuUtilization.gpu  # GPU的计算使用率
                    res = {"gpu_used": gpu_utilization_percentage, "time": start_time}
        print_json(res)
        return res

    return await asyncio.run(asyncio.to_thread(real_func, (pid,)))


async def process_info(pid):
    def real_func(pid):
        start_time = int(time.time())
        process = psutil.Process(int(pid))
        th_number = process.num_handles()
        res = {"process_num_thread": th_number, "time": start_time}
        print_json(res)
        return res

    return await asyncio.run(asyncio.to_thread(real_func, (pid,)))


async def perf(pid, save_dir):
    monitors = {
        "cpu": Monitor(cpu,
                       pid=pid,
                       key_value=["time", "cpu_usage(%)", "cpu_usage_all(%)", "cpu_time", "cpu_core_num(个)"],
                       name="cpu",
                       save_dir=save_dir),
        "memory": Monitor(memory,
                          pid=pid,
                          key_value=["time", "process_memory_usage(M)", "total_memory(M)", "percentage_used(M)"],
                          name="memory",
                          save_dir=save_dir),
        "process_info": Monitor(process_info,
                                pid=pid,
                                key_value=["time", "handle_nums(个)", "threads(个)", "voluntary_ctxt_switches(次)",
                                           "nonvoluntary_ctxt_switches(次)"], name="package_process_info",
                                save_dir=save_dir),
        "fps": Monitor(fps,
                       pid=pid,
                       key_value=["time", "fps(帧)"],
                       name="fps",
                       save_dir=save_dir),
        "gpu": Monitor(gpu,
                       pid=pid,
                       key_value=["time", "gpu(%)"],
                       name="gpu",
                       save_dir=save_dir),
        "screenshot": Monitor(screenshot,
                              pid=pid,
                              name="screenshot",
                              save_dir=save_dir, is_out=False)
    }
    run_monitors = [monitor.run() for name, monitor in monitors.items()]
    await asyncio.gather(*run_monitors)


if __name__ == '__main__':
    asyncio.run(perf(123))