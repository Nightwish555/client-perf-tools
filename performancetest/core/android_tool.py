import asyncio
import re
import time
import traceback
from pathlib import Path
from logzero import logger
from adbutils import adb, AdbDevice
from performancetest.core.monitor import Monitor, print_json


class Fps(object):
    frame_que = list()
    surface_view = None
    before_get_view_data_status = False  # 上次获取view的结果，如果拿到了结果就不用修改view
    single_instance = None

    @staticmethod
    def check_queue_head_frames_complete():
        if not Fps.frame_que:
            return False
        head_time = int(Fps.frame_que[0])
        end_time = int(Fps.frame_que[-1])
        if head_time == end_time:
            return False
        return True

    @staticmethod
    def pop_complete_fps():
        head_time = int(Fps.frame_que[0])
        complete_fps = []
        while int(Fps.frame_que[0]) == head_time:
            complete_fps.append(Fps.frame_que.pop(0))
        return complete_fps

    def __init__(self, device: AdbDevice, package):
        self.package = package
        self.device = device
        self.device.shell("dumpsys SurfaceFlinger --latency-clear")
        self.device.shell("dumpsys gfxinfo {} reset".format(self.package))
        self.start_collect_time = int(time.time())

    def __new__(cls, *args, **kwargs):
        if not cls.single_instance:
            cls.single_instance = super().__new__(cls)
        cls.single_instance.device, cls.single_instance.package = args
        return cls.single_instance

    async def get_surface_view(self):
        all_list = await asyncio.to_thread(self.device.shell,
                                           "dumpsys SurfaceFlinger --list | grep {0}".format(self.package))
        views = all_list.split("\n")
        cur_view, view_data = await self.get_right_view(views)
        if cur_view:
            Fps.surface_view = cur_view
            Fps.before_get_view_data_status = True
        else:
            Fps.before_get_view_data_status = False
        return cur_view, view_data

    async def get_right_view(self, views):
        views = [i for i in views if not "Background for" in i]
        views_spec = [i for i in views if ("SurfaceView" in i) or ("BLAST" in i)]
        if views_spec:
            views = views_spec
        res = await asyncio.gather(*[self.get_view_res(view) for view in views])
        right_view = None
        right_view_data = []
        for index, view_res in enumerate(res):
            print(view_res)
            if not view_res:
                continue
            if len(view_res) < 127:
                continue
            right_view = views[index]
            right_view_data = view_res
        return right_view, right_view_data

    async def get_view_res(self, view):
        if view:
            res_str = await asyncio.to_thread(self.device.shell, "dumpsys SurfaceFlinger --latency '{0}' ".format(view))
            frames = []
            logger.info("view: {0}, res: {1}".format(view, res_str))
            for i in res_str.split("\n"):
                if len(i.split()) >= 3:
                    if len(i.split()[1]) > 16:
                        continue
                    if float(i.split()[1]) <= 0:
                        continue
                    cur_frame_time = float(i.split()[1]) / 1e9 + self.start_collect_time
                    frames.append(cur_frame_time)
            return frames
        else:
            return []

    async def fps(self):
        while not self.check_queue_head_frames_complete():
            cur_frames = []
            if not Fps.before_get_view_data_status:
                view, cur_frames = await self.get_surface_view()
                logger.info("view {0}, cur_frames {1}".format(view, cur_frames))
            else:
                cur_frames = await self.get_view_res(Fps.surface_view)
            new_frames = [i for i in cur_frames if ((not Fps.frame_que) or (i > Fps.frame_que[-1])) and i != 0]
            if not cur_frames or cur_frames[-1] <= 0:
                Fps.surface_view = None
                Fps.before_get_view_data_status = False
            Fps.frame_que.extend(new_frames)
            await asyncio.sleep(0.8)
        return self.pop_complete_fps()

    async def get_top_activity(self):
        dat = await asyncio.to_thread(self.device.shell, "dumpsys activity top")
        activity_re = re.compile(r'\s*ACTIVITY ([A-Za-z0-9_.$]+)/([A-Za-z0-9_.$]+) \w+')
        # in Android8.0 or higher, the result may be more than one
        m = activity_re.findall(dat)
        return "/".join(m[-1]) if m else -1

    async def gfx_fps(self):
        info = await asyncio.to_thread(self.device.shell, "dumpsys gfxinfo {} framestats ".format(self.package))
        info = info.split("\n")
        data_list = []
        top_activity_info = await self.get_top_activity()
        logger.info("gfx_fps {0}, {1}".format(top_activity_info, info))
        start_data = False  # 控制获取数据
        activity = False  # 控制获取当前activity
        data_list = []
        for i in info:
            if str(top_activity_info) in str(i).strip():
                activity = True
            if activity:
                if "PROFILEDATA" in i:
                    if start_data:
                        start_data = False
                        activity = False
                        break
                    else:
                        start_data = True
                        continue
            if start_data:
                try:
                    data_list.append(int(i.split(",")[13]) / 1e9)
                except ValueError as e:
                    logger.error(e)
        await asyncio.to_thread(self.device.shell, ("dumpsys gfxinfo {} reset ".format(self.package)))
        return data_list


async def list_devices():
    devices = await asyncio.to_thread(adb.device_list)
    res = [i.serial for i in devices]
    print_json(res)
    return res


async def sys_info(device: AdbDevice):
    device_info = device.info
    print_json(device_info)
    return device_info


async def app_list(device: AdbDevice):
    android = device
    all_package_versions = await asyncio.to_thread(android.shell, ['dumpsys', 'package'])
    apps_versions = []
    pattern = re.compile(r"Package \[([^\]]+)].*?versionName=([^\s]+)", re.DOTALL)
    matches = pattern.findall(all_package_versions)
    for package_name, version_name in matches:
        apps_versions.append({"package": package_name.strip(), "version": version_name})
    print_json(apps_versions)
    return apps_versions


async def screenshot(device: AdbDevice, save_dir):
    start_time = int(time.time())
    res = await asyncio.to_thread(device.screenshot)
    dir_instance = Path(save_dir)
    screenshot_dir = dir_instance.joinpath("screenshot")
    screenshot_dir.mkdir(exist_ok=True)
    res.save(screenshot_dir.joinpath(str(start_time) + ".png"))
    return res


PS_DICT = {}  # 公用的获取package与pid对应的dict,实时更新


async def ps(device: AdbDevice, is_out=True):
    cmd_args = "ps -ef"
    process = await asyncio.to_thread(device.shell, cmd_args)
    lines = process.split("\n")
    head = lines[0]
    head = head.strip()
    titles = head.split()
    title_dict = {}
    for index, title in enumerate(titles):
        if title:
            title_dict[index] = title
    res = []
    for line in lines[1:]:
        line = line.strip()
        items = line.split()
        tmp_dict = {}
        for index, value in enumerate(items):
            if index in title_dict:
                tmp_dict[title_dict[index]] = value
        if tmp_dict:
            res.append(tmp_dict)
    res.sort(key=lambda x: int(x.get("PID")))
    if is_out:
        print_json(res)
    global PS_DICT
    PS_DICT = res
    return res


async def get_pid(package):
    filter_pids = [i.get("PID") for i in PS_DICT if package in i.get("CMD")]
    if filter_pids:
        return filter_pids[0]
    else:
        return None


async def get_sdk_version(device: AdbDevice):
    sdk_version = device.shell("getprop ro.build.version.sdk")
    return 25 if not sdk_version else int(sdk_version)


async def cpu(device: AdbDevice, package):
    start_time = int(time.time())

    async def get_process_cpu_stat():
        """get the cpu usage of a process at a certain time"""
        cmd = 'cat /proc/{}/stat'.format(process_id)
        result = await asyncio.to_thread(device.shell, cmd)
        result = result.strip()
        toks = re.split("\\s+", result)
        process_cpu = sum(float(toks[i]) for i in range(13, 17))
        return process_cpu

    async def get_total_cpu_stat():
        """get the total cpu usage at a certain time"""
        cmd = 'cat /proc/stat | grep ^cpu'
        result = await asyncio.to_thread(device.shell, cmd)
        result = result.strip()
        total_cpu = 0
        lines = result.split('\n')
        for line in lines:
            toks = line.split()
            if (toks[1] not in ('', ' ')):
                total_cpu += sum(float(toks[i]) for i in range(1, 8))
        return total_cpu

    async def get_idle_cpu_stat():
        """get the total cpu usage at a certain time"""
        cmd = 'cat /proc/stat | grep ^cpu'
        result = await asyncio.to_thread(device.shell, cmd)
        result = result.strip()
        idle_cpu = 0
        lines = result.split('\n')
        for line in lines:
            toks = line.split()
            if (toks[1] not in ('', ' ')):
                idle_cpu += float(toks[4])
        return idle_cpu

    async def get_android_cpu_rate():
        """get the Android cpu rate of a process"""
        try:
            star_before = time.time()
            process_cpu_time_1 = await get_process_cpu_stat()
            total_cpu_time_1 = await get_total_cpu_stat()
            idle_cpu_time_1 = await get_idle_cpu_stat()
            custom_time = time.time() - star_before
            sleep_time = (1 - (custom_time * 2) - 0.02)
            sleep_time = 0.5 if sleep_time <= 0.5 else sleep_time
            await asyncio.sleep(sleep_time)
            process_cpu_time_2 = await get_process_cpu_stat()
            total_cpu_time_2 = await get_total_cpu_stat()
            idle_cpu_time_2 = await get_idle_cpu_stat()
            app_cpu_rate = round(
                float((process_cpu_time_2 - process_cpu_time_1) / (total_cpu_time_2 - total_cpu_time_1) * 100), 2)
            sys_cpu_rate = round(
                float(((total_cpu_time_2 - idle_cpu_time_2) - (total_cpu_time_1 - idle_cpu_time_1)) /
                      (total_cpu_time_2 - total_cpu_time_1) * 100), 2)
        except Exception as e:
            app_cpu_rate, sys_cpu_rate = 0, 0
            logger.error(e)
        return app_cpu_rate, sys_cpu_rate

    process_id = await get_pid(package)
    if process_id:
        app_cpu_rate, sys_cpu_rate = await get_android_cpu_rate()
        res = {"type": "cpu", "cpu_rate": app_cpu_rate, "sys_cpu_rate": sys_cpu_rate, "time": start_time}
    else:
        res = {"type": "cpu", "cpu_rate": 0, "sys_cpu_rate": 0, "time": start_time}
    print_json(res)
    return res


async def memory(device: AdbDevice, package):
    start_time = int(time.time())

    async def get_memory():
        """Get the Android memory information, unit: MB"""
        patterns = {
            'Java Heap': 'Java Heap:\s*(\d+)',
            'Native Heap': 'Native Heap:\s*(\d+)',
            'Code': 'Code:\s*(\d+)',
            'Stack': 'Stack:\s*(\d+)',
            'Graphics': 'Graphics:\s*(\d+)',
            'Private Other': 'Private Other:\s*(\d+)',
            'System': 'System:\s*(\d+)',
        }
        try:
            cmd = 'dumpsys meminfo {}'.format(process_id)
            output = await asyncio.to_thread(device.shell, cmd)
            total_match = re.search(r'TOTAL:\s*(\d+)', output)
            swap_match = re.search(r'TOTAL SWAP PSS:\s*(\d+)', output)
            total_pass = round(float(total_match.group(1)) / 1024, 2)
            swap_pass = round(float(swap_match.group(1)) / 1024, 2)
            memory_detail = {}
            for key, pattern in patterns.items():
                match = re.search(pattern, output)
                memory_detail[key.lower().replace(' ', '_')] = round(float(match.group(1)) / 1024, 2)
        except Exception as e:
            total_pass, swap_pass = 0, 0
            memory_detail = {key.lower().replace(' ', '_'): 0 for key in patterns}
            traceback.print_exc()
        res = {"type": "memory", "total_memory": total_pass, "swap_memory": swap_pass, "time": start_time}
        res.update(memory_detail)
        return res

    process_id = await get_pid(package)
    if process_id:
        memory_info = await get_memory()
    else:
        memory_info = {"type": "memory", "total_memory": 0, "swap_memory": 0, "memory_detail": {}, "time": start_time}
    print_json(memory_info)
    return memory_info


async def package_process_info(device: AdbDevice, package):
    start_time = int(time.time())
    process_id = await get_pid(package)
    if process_id:
        cmd = "cat /proc/{0}/status | grep -E 'Threads:|FDSize:|voluntary_ctxt_switches:|nonvoluntary_ctxt_switches:'".format(
            process_id)
        data = await asyncio.to_thread(device.shell, cmd)
        info_pairs = re.findall(r'(\w+):\s*([^\s]+)', data)
        info_dict = {"handle_nums" if k == "FDSize" else k.lower(): v for k, v in dict(info_pairs).items()}
    else:
        info_dict = {}
    info_dict["type"] = "package_process_info"
    info_dict["time"] = start_time
    print_json(info_dict)
    return info_dict


async def fps(device: AdbDevice, package):
    frames = await Fps(device, package).fps()
    res = {"type": "fps", "fps": len(frames), "frames": frames, "time": int(frames[0])}
    print_json(res)
    return res


async def gpu(device: AdbDevice):
    start_time = int(time.time())
    cmd = 'cat /sys/class/kgsl/kgsl-3d0/gpubusy'
    output = await asyncio.to_thread(device.shell, cmd)
    output = output.strip()
    res_n = output.split(" ")
    for i in range(len(res_n) - 1, -1, -1):
        if res_n[i] == '':
            res_n.pop(i)
    gpu_info = 0
    try:
        gpu_info = int(res_n[0]) / int(res_n[1]) * 100
    except Exception as e:
        logger.error(e)
    res = {"type": "gpu", "gpu": gpu_info, "time": start_time}
    print_json(res)
    return res


async def battery(device: AdbDevice):
    start_time = int(time.time())
    device_battery_info = await asyncio.to_thread(device.shell, "dumpsys battery")
    pattern = re.compile(r'\s*([^:]+):\s*(\S+)', re.MULTILINE)
    matches = pattern.findall(device_battery_info)
    result_dict = dict(matches)
    result_dict["type"] = "battery"
    result_dict["time"] = start_time
    try:
        result_dict["temperature"] = int(result_dict["temperature"]) / 10
    except:
        result_dict["temperature"] = 0
    print_json(result_dict)
    return result_dict


async def shell(device: AdbDevice, cmd):
    return await asyncio.to_thread(device.shell, cmd)


async def perf(device: AdbDevice, package, save_dir):
    monitors = {
        "cpu": Monitor(cpu,
                       device=device,
                       package=package,
                       key_value=["time", "cpu_rate(%)", "sys_cpu_rate(%)"],
                       name="cpu",
                       save_dir=save_dir),
        "memory": Monitor(memory,
                          device=device,
                          package=package,
                          key_value=["time", "total_memory(M)", "swap_memory(M)", "java_heap(M)", "native_heap(M)",
                                     "code(M)", "stack(M)", "graphics(M)", "private_other(M)", "system(M)"],
                          name="memory",
                          save_dir=save_dir),
        "package_process_info": Monitor(package_process_info,
                                        device=device,
                                        package=package,
                                        key_value=["time", "handle_nums(个)", "threads(个)", "voluntary_ctxt_switches(次)",
                                                   "nonvoluntary_ctxt_switches(次)"], name="package_process_info",
                                        save_dir=save_dir),
        "fps": Monitor(fps,
                       device=device,
                       package=package,
                       key_value=["time", "fps(帧)"],
                       name="fps",
                       save_dir=save_dir),
        "battery": Monitor(battery,
                           device=device,
                           key_value=["time", "temperature(℃)", "level(%)"],
                           name="battery",
                           save_dir=save_dir),
        "gpu": Monitor(gpu,
                       device=device,
                       key_value=["time", "gpu(%)"],
                       name="gpu",
                       save_dir=save_dir),
        "screenshot": Monitor(screenshot,
                              device=device,
                              name="screenshot",
                              save_dir=save_dir, is_out=False),
        "ps": Monitor(ps,
                      device=device,
                      is_out=False)
    }
    await ps(device=device, is_out=False)  # 提前给PS_DICT值
    run_monitors = [monitor.run() for name, monitor in monitors.items()]
    await asyncio.gather(*run_monitors)
