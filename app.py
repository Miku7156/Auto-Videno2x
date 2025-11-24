import os
import subprocess
import configparser
import sys
import time
import logging
import json 
from datetime import datetime, timedelta
import re
import shutil
from collections import defaultdict
import threading
import time
            

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
    config.read('config.ini')
    max_log_lines = int(config.get('Logs', 'MaxLogLines', fallback=6000))
    if os.path.exists(log_file):
        with open(log_file, 'r', encoding='utf-8') as f:
            log_lines = f.readlines()
        if len(log_lines) > max_log_lines:
            with open(log_file, 'w', encoding='utf-8') as f:
                f.writelines(log_lines[-max_log_lines:])

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)  # 可选，调试用
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
# 读取帧率增强配置
frame_multiplier = config.get('FrameEnhancement', 'FrameMultiplier')
frame_processor = config.get('FrameEnhancement', 'Processor')
rife_model = config.get('FrameEnhancement', 'RifeModel')
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
        # 2.1 收集所有季度和集数组合
        all_episodes = set()
        for file in files:
            if file["季度信息"] != "00" and file["集数信息"] != "00":
                all_episodes.add((file["季度信息"], file["集数信息"]))

        # 2.2 计算每个分支的文件大小总和
        branch_sizes = defaultdict(int)
        for branch_id, branch_files in enumerate(branches):
            total_size = 0
            seen_episodes = set()
            for file in branch_files:
                # 排除包含Viden2x_HQ的文件
                if "Viden2x_HQ" in file["文件名带扩展名"]:
                    continue
                episode_key = (file["季度信息"], file["集数信息"])
                # 只计算在所有分支中都存在的季度和集数，且每个集数只计算一次
                if episode_key in all_episodes and episode_key not in seen_episodes:
                    # 检查该季度和集数是否存在于所有分支
                    exists_in_all = True
                    for check_branch_id in range(len(branches)):
                        if check_branch_id == branch_id:
                            continue
                        found = False
                        for check_file in branches[check_branch_id]:
                            if (check_file["季度信息"], check_file["集数信息"]) == episode_key:
                                found = True
                                break
                        if not found:
                            exists_in_all = False
                            break
                    if exists_in_all:
                        total_size += file["文件大小 (字节)"]
                        seen_episodes.add(episode_key)
                    branch_sizes[branch_id] = total_size

        # 2.3 按大小排序分支并分配优先级
        # 创建分支ID到索引的映射，提高查找效率
        branch_index_map = {id(branch): idx for idx, branch in enumerate(branches)}
        # 过滤掉分支级为-1的分支
        filtered_branches = [branch for branch in branches if all(file["分支"] != -1 for file in branch)]
        sorted_branches = sorted(filtered_branches, key=lambda x: branch_sizes[branch_index_map[id(x)]])
        for priority, branch in enumerate(sorted_branches):
            for file in branch:
                file["处理优先级"] = priority

    logger.info(f"✅ 数据处理完成，共处理 {len(file_data_list)} 个文件")

    # 新旧扫描结果对比，只保留新增或修改的文件
    if os.path.exists(output_json_path):
        try:
            with open(output_json_path, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
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
        with open(output_json_path, 'w', encoding='utf-8') as f:
            json.dump(file_data_list, f, ensure_ascii=False, indent=2)
        logger.info("💾 结果已保存到: %s", output_json_path)
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
            with open(output_json_path, 'w', encoding='utf-8') as f:
                json.dump(file_data_list, f, ensure_ascii=False, indent=2)
            logger.info("💾 结果已保存到: %s", output_json_path)
            sys.exit(0)
    except subprocess.CalledProcessError as e:
        logger.warning(f"获取GPU使用率失败: {e}，将继续执行程序")
    except (ValueError, Exception) as e:
        logger.warning(f"GPU检查异常: {e}，将继续执行程序")

    #  进行画面增强处理
    for file in file_data_list:
        if file.get("处理步骤") == 1:
            input_path = file["文件完整路径"]
            filename = os.path.basename(input_path)
            output_path = os.path.join(tmp_dir, filename)
            logger.info(f"开始增强画面: {input_path}")
            # 显示加载动画
            def loading_animation(stop_event):
                while not stop_event.is_set():
                    for char in '|/-\ ':
                        print(f'\r正在增强画面中... {char}', end='', flush=True)
                        time.sleep(0.1)
                print('\r增强画面完成!        ', flush=True)
            
            stop_event = threading.Event()
            loading_thread = threading.Thread(target=loading_animation, args=(stop_event,))
            loading_thread.start()
            try:
                start_time = time.time()
                subprocess.run([
                    video2x_path,
                    '-i', input_path,
                    '-o', output_path,
                    '-w', res_width,
                    '-h', res_height,
                    '-p', res_processor,
                    '--libplacebo-shader', res_shader
                ], capture_output=True, text=True)
                end_time = time.time()
                duration = end_time - start_time
                logger.info(f"画面增强完成:{output_path},耗时: {duration:.2f}秒")
                file['处理步骤'] = 2  # 标记为已增强
                #保存数据
                with open(output_json_path, 'w', encoding='utf-8') as f:
                    json.dump(file_data_list, f, ensure_ascii=False, indent=2)
                logger.info("💾 结果已保存到: %s", output_json_path)
            except subprocess.CalledProcessError as e:
                logger.error(f"处理文件 {input_path} 失败: {e}")
            finally:
                stop_event.set()
                loading_thread.join()

        # 帧率增强处理：处理步骤=2的文件
        if file.get('处理步骤') == 2:
            input_filename = os.path.basename(file['文件完整路径'])
            input_path = os.path.join(tmp_dir, input_filename)
            
            # 检查临时文件夹中是否存在已增强的文件
            if not os.path.exists(input_path):
                logger.warning(f"临时文件不存在，跳过帧率增强: {input_path}")
                continue
            
            # 构建新文件名
            base_name, ext = os.path.splitext(input_filename)
            new_filename = f"{base_name} {res_width}x{res_height} fpsx{frame_multiplier} Viden2x_HQ{ext}"
            output_path = os.path.join(tmp_dir, new_filename)
            
            try:
                logger.info(f"开始帧率增强: {input_path}")
                start_time = time.time()
                # 显示加载动画
                def loading_animation(stop_event):
                    while not stop_event.is_set():
                        for char in '|/-\ ':
                            print(f'\r正在增强帧率中... {char}', end='', flush=True)
                            time.sleep(0.1)
                    print('\r增强帧率完成!        ', flush=True)
                stop_event = threading.Event()
                loading_thread = threading.Thread(target=loading_animation, args=(stop_event,))
                loading_thread.start()
                # 执行video2x帧率增强命令
                # 添加详细日志和错误捕获
                result = subprocess.run([
                    video2x_path,
                    'upscale',  # 显式指定upscale命令
                    '-i', input_path,
                    '-o', output_path,
                    '-m', frame_multiplier,
                    '-p', frame_processor,
                    '--rife-model', rife_model,
                ], capture_output=True, text=True)
                # 处理输出内容，区分GPU信息和错误
                stderr_lines = result.stderr.splitlines()
                gpu_lines = [line for line in stderr_lines if '[0 NVIDIA GeForce' in line]
                non_gpu_lines = [line for line in stderr_lines if '[0 NVIDIA GeForce' not in line and line.strip()]
                # 检查命令执行结果
                if result.returncode == 0:
                    # 命令成功执行
                    if non_gpu_lines:
                        logger.warning(f"帧率增强成功但存在输出: {chr(10).join(non_gpu_lines)}")
                    # 计算处理时间
                    end_time = time.time()
                    stop_event.set()
                    loading_thread.join()
                    duration = end_time - start_time
                    logger.info(f"帧率增强完成:{output_path},耗时: {duration:.2f}秒")
                    file['处理步骤'] = 3    #标记为已执行完全部处理
                    # 验证输出文件完整性
                    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                        logger.info(f"帧率增强文件验证成功: {output_path}")
                        # 将文件移动到原文件目录
                        original_dir = os.path.dirname(file['文件完整路径'])
                        target_path = os.path.join(original_dir, new_filename)
                        try:
                            os.makedirs(original_dir, exist_ok=True)
                            shutil.move(output_path, target_path)
                            logger.info(f"帧率增强文件已移动至: {target_path}")
                            # 更新文件记录路径和处理状态
                            file['处理步骤'] = 3  # 标记为已完成所有处理
                            # 清理临时画面增强文件
                            if os.path.exists(input_path):
                                os.remove(input_path)
                                logger.info(f"已清理临时画面增强文件: {input_path}")
                        except Exception as e:
                            logger.error(f"文件移动或清理失败: {str(e)}")
                    else:
                        logger.error(f"帧率增强文件验证失败: {output_path} 不存在或为空")
                        file['处理步骤'] = 2  # 重置处理步骤以便重试
                else:
                    # 命令返回非零退出码
                    if not non_gpu_lines and gpu_lines:
                        # 仅包含GPU信息，视为成功
                        # 计算处理时间
                        end_time = time.time()
                        duration = end_time - start_time
                        logger.info(f"帧率增强完成:{output_path},耗时: {duration:.2f}秒")
                        file['处理步骤'] = 3   #标记为已执行完全部处理
                        # 验证输出文件完整性
                        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                            logger.info(f"帧率增强文件验证成功: {output_path}")
                            # 将文件移动到原文件目录
                            original_dir = os.path.dirname(file['文件完整路径'])
                            target_path = os.path.join(original_dir, new_filename)
                            try:
                                os.makedirs(original_dir, exist_ok=True)
                                shutil.move(output_path, target_path)
                                logger.info(f"帧率增强文件已移动至: {target_path}")
                                # 更新文件记录路径和处理状态
                                file['处理步骤'] = 3  # 标记为已完成所有处理
                                # 清理临时画面增强文件
                                if os.path.exists(input_path):
                                    os.remove(input_path)
                                    logger.info(f"已清理临时画面增强文件: {input_path}")
                            except Exception as e:
                                logger.error(f"文件移动或清理失败: {str(e)}")
                        else:
                            logger.error(f"帧率增强文件验证失败: {output_path} 不存在或为空")
                            file['处理步骤'] = 2  # 重置处理步骤以便重试
                    else:
                        # 真正的错误，记录并抛出异常
                        logger.error(f"帧率增强失败: 退出代码 {result.returncode}, 命令: {result.args}")
                        if result.stdout:
                            logger.error(f"标准输出: {result.stdout}")
                        if non_gpu_lines:
                            logger.error(f"错误输出: {chr(10).join(non_gpu_lines)}")
                        else:
                            logger.error(f"错误输出: {result.stderr}")
                        raise subprocess.CalledProcessError(result.returncode, result.args)
            except subprocess.CalledProcessError as e:
                logger.error(f"帧率增强失败: 退出代码 {e.returncode}, 命令: {e.cmd}")
                logger.error(f"标准输出: {getattr(e, 'stdout', '未捕获')}")
                logger.error(f"错误输出: {getattr(e, 'stderr', '未捕获')}")
            except Exception as e:
                logger.error(f"帧率增强发生异常: {str(e)}")

            finally:
                stop_event.set()
                loading_thread.join()
    #保存数据
    with open(output_json_path, 'w', encoding='utf-8') as f:
        json.dump(file_data_list, f, ensure_ascii=False, indent=2)
    logger.info("💾 结果已保存到: %s", output_json_path)
    # 检查是否启用自动关机
    auto_shutdown = config.getboolean('Schedule', 'AutoShutdown', fallback=False)
    if auto_shutdown:
        logger.info("所有任务已完成，准备关闭电脑...")
        subprocess.run(["powershell", "Stop-Computer", "-Force"])
        sys.exit(0)
    else:
        logger.info("所有任务已完成，自动关机功能已禁用")
        sys.exit(0)

except Exception as e:
    logger.critical("💥 扫描过程中发生严重错误: %s", e, exc_info=True)
    sys.exit(1)
