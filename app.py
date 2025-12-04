# -*- coding: utf-8 -*-
import os
import subprocess
import configparser
import sys
import time
import logging
import json
from datetime import datetime, timedelta
import re
from collections import defaultdict
import signal
from data_manager import DataManager
import io

# 确保标准输出和错误输出使用UTF-8编码
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')




# 信号处理器
def signal_handler(sig, frame):
    print('\n接收到停止信号，正在停止程序...')
    sys.exit(0)

# 注册信号处理器
signal.signal(signal.SIGINT, signal_handler)
            

# 视频文件扩展名集合
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.m4v', '.mpeg', '.mpg', '.ts', '.webm', '.vob', '.ogv', '.rmvb', '.asf', '.rm', '.3gp'}

# -------------------------------
# 1. 日志配置（务必放在最前面）
# -------------------------------

def get_base_dir():
    """获取基础目录，兼容PyInstaller打包后的环境"""
    if hasattr(sys, '_MEIPASS'):
        # 打包后返回exe所在目录
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_dir()

def setup_logging():
    log_dir = os.path.join(BASE_DIR, 'log')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'app.log')

    # 从配置文件读取日志行数限制
    config = configparser.ConfigParser()
    config.read('config.ini', encoding='utf-8')
    max_log_lines = int(config.get('Logs', 'MaxLogLines', fallback=6000))
    if os.path.exists(log_file):
        with open(log_file, 'r', encoding='utf-8') as f:
            log_lines = f.readlines()
        if len(log_lines) > max_log_lines:
            with open(log_file, 'w', encoding='utf-8') as f:
                f.writelines(log_lines[-max_log_lines:])

    # 设置控制台编码以正确显示中文
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.stream.reconfigure(encoding='utf-8')
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            console_handler
        ]
    )

setup_logging()
logger = logging.getLogger(__name__)
# -------------------------------
# 2. 基础路径 & 配置读取
# -------------------------------
CONFIG_FILE = os.path.join(BASE_DIR, 'config.ini')
DATA_DIR = os.path.join(BASE_DIR, 'data')

config = configparser.ConfigParser()
config.read(CONFIG_FILE, encoding='utf-8')

scan_path = config.get('PATHS', 'ScanPath', fallback=None)

# 读取视频处理配置
# 读取分辨率增强配置
res_width = config.get('ResolutionEnhancement', 'ResolutionWidth')
res_height = config.get('ResolutionEnhancement', 'ResolutionHeight')
res_processor = config.get('ResolutionEnhancement', 'Processor')
res_shader = config.get('ResolutionEnhancement', 'Shader')
# 读取分辨率增强编码配置
res_encoder = config.get('ResolutionEnhancement', 'Encoder', fallback='h264_nvenc')
res_preset = config.get('ResolutionEnhancement', 'EncoderPreset', fallback='p7')
res_crf = config.get('ResolutionEnhancement', 'EncoderCRF', fallback='24')
# 读取帧率增强配置
frame_multiplier = config.get('FrameEnhancement', 'FrameMultiplier')
frame_processor = config.get('FrameEnhancement', 'Processor')
rife_model = config.get('FrameEnhancement', 'RifeModel')
# 读取帧率增强编码配置
frame_encoder = config.get('FrameEnhancement', 'Encoder', fallback='h264_nvenc')
frame_preset = config.get('FrameEnhancement', 'EncoderPreset', fallback='p7')
frame_crf = config.get('FrameEnhancement', 'EncoderCRF', fallback='26')
# 读取线程数配置
threads = config.get('FrameEnhancement', 'Threads', fallback='100')
tmp_dir = config.get('PATHS', 'TmpDir')
tmp_dir = os.path.join(BASE_DIR, tmp_dir)
os.makedirs(tmp_dir, exist_ok=True)

# 读取video2x路径配置
video2x_path = config.get('PATHS', 'Video2xPath')
if not video2x_path or not os.path.exists(video2x_path):
    logger.error("❌ 请在config.ini的[PATHS]节中设置有效的Video2xPath路径")
    sys.exit(1)

if not scan_path:
    logger.error("❌ 未在 config.ini 的 [PATHS] 节中找到 ScanPath 配置项")
    sys.exit(1)

if not os.path.exists(scan_path):
    logger.error("❌ 扫描路径不存在: %s", scan_path)
    sys.exit(1)


# -------------------------------
# 3. 清理路径，生成 JSON 文件名
# -------------------------------
def sanitize_path_for_filename(path):
    folder_name = os.path.basename(os.path.normpath(path))
    return folder_name

sanitized_name = sanitize_path_for_filename(scan_path)
json_filename = f"scan_result_{sanitized_name}.json"
output_json_path = os.path.join(DATA_DIR, json_filename)

# 初始化数据管理器
data_manager = DataManager(output_json_path)

# -------------------------------
# 4. 扫描目录并记录日志
# -------------------------------
file_data_list = []

logger.info(f"开始扫描目录: {scan_path}")

try:
    for root, dirs, files in os.walk(scan_path):
        for file in files:
            # 只处理视频文件
            ext = os.path.splitext(file)[1].lower()
            if ext not in VIDEO_EXTENSIONS:
                continue
            try:
                full_path = os.path.join(root, file)
                parent_dir = root
                filename_with_ext = file
                file_size = os.path.getsize(full_path)
                mod_time = os.path.getmtime(full_path)
                mod_time_str = datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M:%S')

                # 从文件名中提取 季度(S01) 和 集数(E06)
                season_match = re.search(r'S(\d{2})E(\d{2,4})', filename_with_ext, re.IGNORECASE)
                season = "00"  # 默认值
                episode = "0000"  # 默认值

                if season_match:
                    season = season_match.group(1)  # 如 '01'
                    episode = season_match.group(2)  # 如 '06'

                file_record = {
                    "父目录": parent_dir,
                    "文件名带扩展名": filename_with_ext,
                    "文件完整路径": full_path,
                    "文件大小 (字节)": file_size,
                    "文件修改时间": mod_time_str,
                    "季度信息": season,
                    "集数信息": episode,
                    "分支": -1,
                    "处理优先级": -1,
                    "处理步骤": 3 if "Viden2x_HQ" in filename_with_ext else 0
                }
                file_data_list.append(file_record)
                logger.debug(f"发现视频文件: {file_record['文件完整路径']}")

            except Exception as e:
                logger.error("⚠️ 处理文件 '%s' 时出错: %s", file, e, exc_info=True)

    logger.info(f"✅ 扫描完成，共发现 {len(file_data_list)} 个视频文件")

    # 按目录分组文件
    dir_groups = {}
    for file in file_data_list:
        dir_path = file["父目录"]
        if dir_path not in dir_groups:
            dir_groups[dir_path] = []
        dir_groups[dir_path].append(file)

    # 计算字符串相似度 (Levenshtein距离)
    def levenshtein_distance(s1, s2):
        if len(s1) < len(s2):
            return levenshtein_distance(s2, s1)
        if len(s2) == 0:
            return len(s1)
        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        return previous_row[-1]

    # 裁剪文件名到相同长度，保留后缀名
    def trim_filenames(name1, name2):
        base1, ext1 = os.path.splitext(name1)
        base2, ext2 = os.path.splitext(name2)
        
        target_len = min(len(name1), len(name2))
        
        # 处理第一个文件名
        if len(name1) > target_len:
            base_len = target_len - len(ext1)
            base1 = base1[:base_len] if base_len > 0 else ''
            name1 = f"{base1}{ext1}"
        
        # 处理第二个文件名
        if len(name2) > target_len:
            base_len = target_len - len(ext2)
            base2 = base2[:base_len] if base_len > 0 else ''
            name2 = f"{base2}{ext2}"
        
        return name1, name2

    # 处理每个目录
    for dir_path, files in dir_groups.items():
        # 1. 文件归类 (分支)
        branches = []
        ungrouped = [file for file in files if "Viden2x_HQ" not in file["文件完整路径"]]
        while ungrouped:
            current_file = ungrouped.pop(0)
            current_group = [current_file]
            current_name = current_file["文件名带扩展名"]
            current_ext = os.path.splitext(current_name)[1]
            current_len = len(current_name)

            # 比较剩余文件
            to_remove = []
            for i, file in enumerate(ungrouped):
                name = file["文件名带扩展名"]
                ext = os.path.splitext(name)[1]
                name_len = len(name)

                # 检查后缀名是否相同
                if ext != current_ext:
                    continue

                # 检查文件名长度差异
                if abs(name_len - current_len) > 5:
                    continue

                # 检查文件名相似度
                # 移除文件名中的季度和集数信息 (SxxExx格式)
                pattern = re.compile(r'S\d{2,}E\d{2,}', re.IGNORECASE)
                cleaned_current = pattern.sub('', current_name)
                cleaned_name = pattern.sub('', name) 
                distance = levenshtein_distance(cleaned_current, cleaned_name)
                similarity = 1 - (distance / max(len(current_name), len(name)))
                if similarity > 0.6:
                    current_group.append(file)
                    to_remove.append(i)

            # 将同一组的文件标记相同分支
            branch_id = len(branches)
            for file in current_group:
                file["分支"] = branch_id
            branches.append(current_group)

            # 从待分组列表中移除已分组文件
            for i in reversed(to_remove):
                ungrouped.pop(i)

        # 处理小分支合并 - 将文件数≤2的分支合并到相似度最高的大分支
        small_branches = [b for b in branches if len(b) <= 2]
        large_branches = [b for b in branches if len(b) >= 3]

        # 仅当存在大分支时才合并小分支
        if large_branches and small_branches:
            # 创建新分支列表，以大分支为基础
            new_branches = large_branches.copy()
            
            for small_branch in small_branches:
                # 取小分支的第一个文件作为代表
                small_rep = small_branch[0]
                small_name = small_rep["文件名带扩展名"]
                min_distance = float('inf')
                best_branch = None

                # 在新分支列表中找到相似度最高的大分支（不设置匹配阈值）
                for candidate_branch in new_branches:
                    # 取候选分支的第一个文件作为代表
                    rep_name = candidate_branch[0]["文件名带扩展名"]
                     # 裁剪文件名到相同长度，保留后缀名
                    small_name_trimmed, rep_name_trimmed = trim_filenames(small_name, rep_name)
                     # 计算裁剪后的字符串相似度
                    distance = levenshtein_distance(small_name_trimmed, rep_name_trimmed)
                    if distance < min_distance:
                        min_distance = distance
                        best_branch = candidate_branch
                # 将小分支合并到最佳匹配的大分支（不设置相似度阈值）
                # 无条件合并到最相似的大分支
                branch_id = new_branches.index(best_branch)

                for file in small_branch:
                    file["分支"] = branch_id
                best_branch.extend(small_branch)
        
            # 更新分支列表为合并后的新分支
            branches = new_branches

        # 2. 计算处理优先级
        # 2.3 按新的排序逻辑分配优先级
        # 过滤掉分支级为-1的分支
        filtered_branches = [branch for branch in branches if all(file["分支"] != -1 for file in branch)]
        
        def get_file_sort_key(file):
            """获取文件排序键：按修改时间排序"""
            try:
                modify_time = datetime.strptime(file["文件修改时间"], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                # 如果无法解析时间，使用当前时间
                modify_time = datetime.now()
            return modify_time
        
        # 新的排序逻辑：
        # 1. 按季度和集数组合对所有分支中的文件分组
        # 2. 对每组相同季度和集数的文件按修改时间排序
        # 3. 统计每个分支中具有最早修改时间的文件数量
        # 4. 按照这个数量进行排序，数量多的分支优先级更高
        
        # 按季度和集数组合对所有文件分组
        # 只有在所有分支中都存在的季度和集数组合才会被纳入排序考虑
        episode_groups = {}  # {(季度, 集数): [文件列表]}
        
        # 首先统计每个季度和集数组合出现在多少个分支中
        episode_branch_counts = {}
        branch_count = len(filtered_branches)
        
        for branch in filtered_branches:
            # 使用集合来避免同一分支中重复的季度和集数组合被多次计算
            branch_episode_keys = set()
            for file in branch:
                episode_key = (file["季度信息"], file["集数信息"])
                branch_episode_keys.add(episode_key)
            
            # 增加每个季度和集数组合的分支计数
            for episode_key in branch_episode_keys:
                episode_branch_counts[episode_key] = episode_branch_counts.get(episode_key, 0) + 1
        
        # 只保留那些在所有分支中都存在的季度和集数组合
        valid_episode_keys = {key for key, count in episode_branch_counts.items() if count == branch_count}
        
        # 对所有文件进行分组，但只保留有效的季度和集数组合
        for branch in filtered_branches:
            for file in branch:
                episode_key = (file["季度信息"], file["集数信息"])
                if episode_key in valid_episode_keys:
                    if episode_key not in episode_groups:
                        episode_groups[episode_key] = []
                    episode_groups[episode_key].append(file)
        
        # 对每组相同季度和集数的文件按修改时间排序
        for episode_key, files in episode_groups.items():
            files.sort(key=get_file_sort_key)
        
        # 统计每个分支中具有最早修改时间的文件数量
        # （即在各自季度和集数组合中时间最早的文件）
        branch_early_file_counts = [0] * len(filtered_branches)
        # 创建一个从文件路径到分支索引的映射，提高查找效率
        file_to_branch_index = {}
        for branch_idx, branch in enumerate(filtered_branches):
            for file in branch:
                file_to_branch_index[file["文件完整路径"]] = branch_idx
                
        # 对于每个episode group，只统计最早修改的那个文件所属的分支
        for episode_key, files in episode_groups.items():
            if files:  # 确保组中有文件
                # files已经按修改时间排序，第一个就是最早修改的文件
                earliest_file = files[0]
                # 找到该文件所属的分支
                branch_idx = file_to_branch_index.get(earliest_file["文件完整路径"])
                if branch_idx is not None:
                    branch_early_file_counts[branch_idx] += 1
        
        # 根据每个分支中早期文件的数量排序分支
        # 数量多的分支优先级更高（处理优先级数字更小）
        branch_indices = list(range(len(filtered_branches)))
        # 按早期文件数量降序排列（使用负数实现降序）
        sorted_indices = sorted(branch_indices, key=lambda i: -branch_early_file_counts[i])
        
        # 根据排序结果分配处理优先级，确保同一分支内的文件具有相同的优先级
        # 修改优先级分配逻辑，使早期文件数量多的分支优先级数字更小（优先级更高）
        # 优先级从0开始分配，数值越小优先级越高
        for priority, branch_idx in enumerate(sorted_indices):
            branch = filtered_branches[branch_idx]
            for file in branch:
                file["处理优先级"] = priority

    logger.info(f"✅ 数据处理完成，共处理 {len(file_data_list)} 个文件")

    # 新旧扫描结果对比，只保留新增或修改的文件
    if os.path.exists(output_json_path):
        try:
            with open(output_json_path, 'r', encoding='utf-8') as f:
                old_data = data_manager.load_data()
            # 过滤旧数据中实际文件不存在的条目
            old_data = [file for file in old_data if os.path.exists(file['文件完整路径'])]
            # 创建旧数据的路径到文件信息的映射（统一转为小写路径，避免大小写问题）
            # 创建旧数据的路径+大小组合键映射（用于判断新增文件）
            old_file_keys = set()
            for file in old_data:
                path = file['文件完整路径'].lower()
                size = file['文件大小 (字节)']
                key = f"{path}_{size}"
                old_file_keys.add(key)
            
            # 筛选新增文件（路径+大小组合不存在于旧数据中）
            new_files = []
            for new_file in file_data_list:
                path = new_file['文件完整路径'].lower()
                size = new_file['文件大小 (字节)']
                key = f"{path}_{size}"
                if key not in old_file_keys:
                    new_files.append(new_file)
            
            # 追加新增文件到旧数据中
            file_data_list = old_data + new_files
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"加载旧扫描结果失败: {e}，将保存完整扫描结果")
            # 加载失败时使用完整扫描结果
            pass

    os.makedirs(DATA_DIR, exist_ok=True)
    # 筛选6天内更新且处理优先级==0、处理步骤==0的文件，标记处理步骤为1（已筛选）
    current_time = datetime.now()
    six_days_ago = current_time - timedelta(days=6)
    filtered_count = 0
    filtered_files = []
    for file in file_data_list:
        if file.get("处理优先级") == 0 and file.get("处理步骤") == 0:
            try:
                modify_time = datetime.strptime(file["文件修改时间"], '%Y-%m-%d %H:%M:%S')
                if modify_time >= six_days_ago:
                    file["处理步骤"] = 1  # 标记为已筛选
                    filtered_count += 1
                    filtered_files.append(file["文件完整路径"])
            except ValueError:
                logger.warning(f"无法解析文件修改时间: {file['文件修改时间']}")
    # 输出筛选结果统计
    logger.info(f"筛选出 {filtered_count} 个符合条件的文件:")
    for file_path in filtered_files:
        logger.info(f"- {file_path}")
    # 保存数据
    data_manager.save_data(file_data_list)
    
    # 读取分辨率增强倍数配置
    res_multiplier = config.get('Video', 'res_multiplier', fallback='2')
    # 开始画面增强处理
    # 检查是否在允许的时间范围内执行
    allowed_days = config.get('Schedule', 'AllowedDays', fallback='1-7')
    start_day, end_day = map(int, allowed_days.split('-'))
    current_day = datetime.now().weekday() + 1  # Python中周一为0，转换为1-7

    if not (start_day <= current_day <= end_day):
        logger.info(f"当前星期 {current_day} 不在允许的执行时间范围 {allowed_days} 内，保存数据并退出程序")
        # 保存数据
        data_manager.save_data(file_data_list)
        sys.exit(0)
    
    # 检查GPU占用度
    gpu_threshold = config.getint('Schedule', 'GpuUsageThreshold', fallback=80)
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, check=True
        )
        gpu_usage = int(result.stdout.strip())
        logger.info(f"当前GPU使用率: {gpu_usage}%")

        if gpu_usage > gpu_threshold:
            logger.info(f"GPU占用度 {gpu_usage}% 超过阈值 {gpu_threshold}%，保存数据并退出程序")
            # 保存数据
            data_manager.save_data(file_data_list)
            sys.exit(0)
    except subprocess.CalledProcessError as e:
        logger.warning(f"获取GPU使用率失败: {e}，将继续执行程序")
    except (ValueError, Exception) as e:
        logger.warning(f"GPU检查异常: {e}，将继续执行程序")


    # 移除了系统资源获取函数get_system_resources
    # 因为现在改为直接处理方式，不需要检查系统资源


    # 移除了资源检查和多进程启动函数check_resource_and_launch
    # 现在直接处理视频，无需检查系统资源和进程数量

    # 直接遍历处理每个符合条件的文件（替代原来的多进程方式）
    processed_count = 0
    
    try:
        for file in file_data_list:
            if file.get("处理步骤") == 1 or file.get("处理步骤") == 2:
                # 直接调用video_processor.py中的video_processorn函数处理文件
                # 导入video_processor模块并调用video_processorn函数
                import video_processor
                success = video_processor.video_processorn(
                    file, tmp_dir, video2x_path, res_width, res_height, res_processor,
                    res_shader, res_encoder, res_preset, res_crf, frame_multiplier,
                    frame_processor, rife_model, frame_encoder, frame_preset, frame_crf,
                    threads)
                if success:
                    logger.info(f"成功处理文件: {file.get('文件名带扩展名', '未知文件')}")
                else:
                    logger.error(f"处理文件失败: {file.get('文件名带扩展名', '未知文件')}")
                processed_count += 1
    except Exception as e:
        logger.error(f"处理文件时出错: {e}")
    
    
    
    logger.info(f"总共处理了 {processed_count} 个文件")
    # 由于改为直接处理方式，不再需要等待多进程任务完成
    # 所有视频文件已经在上面的循环中处理完毕
    logger.info("所有视频文件处理完成")

    # 检查是否启用自动关机
    auto_shutdown = config.getboolean('Schedule', 'AutoShutdown', fallback=False)
    if auto_shutdown:
        # 设置日志级别为DEBUG以便查看调试信息
        logging.getLogger().setLevel(logging.DEBUG)
        try:
            import win32api
            import win32con
            from datetime import datetime, timedelta
        except ImportError:
            logger.error("缺少pywin32库，无法监控输入活动，将直接关机")
            subprocess.run(["powershell", "Stop-Computer", "-Force"])
            sys.exit(0)

        def get_last_input_time():
            """获取最后一次输入的时间"""
            try:
                last_input_time = win32api.GetLastInputInfo()
                elapsed_seconds = (win32api.GetTickCount() - last_input_time) / 1000
                result = datetime.now() - timedelta(seconds=elapsed_seconds)
                return result
            except Exception as e:
                # 如果获取失败，返回一个很早的时间，避免误判
                logger.debug(f"获取最后输入时间失败: {e}")
                return datetime.now() - timedelta(hours=1)

        def get_mouse_position():
            """获取当前鼠标位置"""
            try:
                return win32api.GetCursorPos()
            except:
                return (0, 0)

        def is_significant_mouse_movement(last_pos, current_pos, threshold=50):
            """判断鼠标移动是否显著（超过阈值）"""
            if last_pos is None:
                return False
            dx = current_pos[0] - last_pos[0]
            dy = current_pos[1] - last_pos[1]
            distance = (dx*dx + dy*dy) ** 0.5
            return distance >= threshold

        monitor_duration = 15  # 监控时长(分钟)
        check_interval = 60   # 检查间隔(秒)
        mouse_threshold = 50   # 鼠标移动阈值(像素)
        logger.info(f"所有任务已完成，将在{monitor_duration}分钟后关闭电脑，期间检测到输入活动将取消关机")

        shutdown_time = datetime.now() + timedelta(minutes=monitor_duration)
        # 获取程序启动时的最后输入时间作为基准
        baseline_last_input = get_last_input_time()
        # 获取初始鼠标位置
        baseline_mouse_pos = get_mouse_position()
        
        # 等待一小段时间，确保基准时间准确
        time.sleep(1)
        # 重新获取基准时间，避免程序启动时的干扰
        baseline_last_input = get_last_input_time()
        
        while datetime.now() < shutdown_time:
            last_input = get_last_input_time()
            current_mouse_pos = get_mouse_position()
            
            # 检查是否有键盘输入活动
            # 只有当时间差超过一定阈值时才认为是真正的键盘活动
            time_diff = (last_input - baseline_last_input).total_seconds()
            keyboard_activity = time_diff > 5  # 提高阈值到5秒以避免误判
            
            # 检查是否有显著的鼠标移动
            mouse_activity = is_significant_mouse_movement(baseline_mouse_pos, current_mouse_pos, mouse_threshold)
            
            
            # 增加额外的验证，避免误判
            if keyboard_activity:
                # 等待短暂时间再次确认
                time.sleep(0.1)
                confirmed_last_input = get_last_input_time()
                # 使用同样的阈值检查
                confirmed_time_diff = (confirmed_last_input - baseline_last_input).total_seconds()
                keyboard_activity = confirmed_time_diff > 5  # 使用相同的5秒阈值
            
            # 如果检测到键盘输入或显著鼠标移动，则取消关机
            if keyboard_activity or mouse_activity:
                if keyboard_activity:
                    logger.info("检测到键盘输入活动，取消自动关机")
                    # 输出详细的时间差信息
                    time_diff = last_input - baseline_last_input
                elif mouse_activity:
                    logger.info("检测到显著鼠标移动，取消自动关机")
                sys.exit(0)
                
            remaining = (shutdown_time - datetime.now()).seconds // 60
            logger.info(f"无显著输入活动，剩余{remaining}分钟后关机...")
            
            # 在长时间等待前再检查一次鼠标位置，避免累积误差
            if remaining > 0 and check_interval > 30:
                # 如果检查间隔较长，在中间再检查一次
                time.sleep(check_interval // 2)
                mid_mouse_pos = get_mouse_position()
                mid_mouse_activity = is_significant_mouse_movement(baseline_mouse_pos, mid_mouse_pos, mouse_threshold)
                
                if mid_mouse_activity:
                    logger.info("检测到显著鼠标移动，取消自动关机")
                    sys.exit(0)
                
                time.sleep(check_interval - check_interval // 2)
            else:
                time.sleep(check_interval)

        logger.info("监控时间结束，无显著输入活动，准备关闭电脑...")
        subprocess.run(["powershell", "Stop-Computer", "-Force"])
        sys.exit(0)
    else:
        logger.info("所有任务已完成，自动关机功能已禁用")
        sys.exit(0)

except Exception as e:
    logger.critical("💥 扫描过程中发生严重错误: %s", e, exc_info=True)
    sys.exit(1)
