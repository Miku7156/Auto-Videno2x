import os
import json
import subprocess
import logging
import time
import shutil
import configparser
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from data_manager import DataManager

def get_base_dir():
    """获取基础目录，兼容PyInstaller打包后的环境"""
    if hasattr(sys, '_MEIPASS'):
        # 打包后返回exe所在目录
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_dir()
# 读取配置
config = configparser.ConfigParser()
config.read('config.ini')

# 读取处理开关配置
enable_resolution_enhancement = config.getboolean('Processing', 'EnableResolutionEnhancement', fallback=True)
enable_frame_enhancement = config.getboolean('Processing', 'EnableFrameEnhancement', fallback=True)

# 读取基础路径
DATA_DIR = os.path.join(BASE_DIR, 'data')
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
scan_path = config.get('PATHS', 'ScanPath', fallback=None)
# 读取video2x路径配置
video2x_path = config.get('PATHS', 'Video2xPath')



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


def process_single_file(file, tmp_dir, video2x_path, res_width, res_height, res_processor, res_shader, res_encoder, res_preset, res_crf, logger):
    """进行画面增强处理"""
    input_path = file["文件完整路径"]
    # 验证输入路径是否存在
    if not os.path.exists(input_path):
        logger.error(f"输入文件不存在: {input_path}")
        return
    
    # 创建tmp/raw目录
    raw_tmp_dir = os.path.join(BASE_DIR, 'tmp', 'raw')
    os.makedirs(raw_tmp_dir, exist_ok=True)

    # 获取文件名
    filename = os.path.basename(input_path)
    
    # 将输入文件复制到tmp/raw目录
    raw_input_path = os.path.join(raw_tmp_dir, filename)
    try:
        # 先判断目录中是否已经有该文件
        if os.path.exists(raw_input_path):
            logger.info(f"文件已存在于临时目录: {raw_input_path}")
        else:
            shutil.copy2(input_path, raw_input_path)
            logger.info(f"文件已复制到临时目录: {raw_input_path}")
    except Exception as e:
        logger.error(f"复制文件到临时目录失败: {e}")
        return

    # 设置输出路径
    output_path = os.path.join(tmp_dir, filename)
    logger.info(f"开始增强画面: {raw_input_path}")
    try:
        start_time = time.time()
        # 使用shell=True并正确引用路径以避免空格问题
        cmd = [
            video2x_path,
            '-i', input_path,
            '-o', output_path,
            '-w', str(res_width),
            '-h', str(res_height),
            '-p', res_processor,
            '--libplacebo-shader', res_shader,
            '-c', res_encoder,
            '-e', f'preset={res_preset}',
            '-e', f'qp={res_crf}',
        ]
        
        # 使用完整的命令字符串并确保路径正确引用
        cmd_str = f'"{video2x_path}" -i "{raw_input_path}" -o "{output_path}" -w {res_width} -h {res_height} -p {res_processor} --libplacebo-shader {res_shader} -c {res_encoder} -e preset={res_preset} -e qp={res_crf}'
        # 优化subprocess调用参数以提高性能，同时保持输出可见
        # 设置环境变量以匹配IDE环境
        env = os.environ.copy()
        env['CUDA_VISIBLE_DEVICES'] = '0'
        env['NVIDIA_VISIBLE_DEVICES'] = 'all'
        # 为了在控制台显示输出，我们不捕获输出，但需要处理可能的异常
        try:
            result = subprocess.run(cmd_str, shell=True, capture_output=False, text=True, encoding='utf-8', errors='ignore', env=env)
        except Exception as e:
            logger.error(f"执行命令时发生异常: {e}")
            raise
        end_time = time.time()
        duration = end_time - start_time
        logger.info(f"画面增强完成:{output_path},耗时: {duration:.2f}秒")
        file['处理步骤'] = 2  # 标记为已增强
        #对数据进行更新
        data_manager.update_record({"文件完整路径": file.get("文件完整路径")},file)
        
        # 清理临时文件
        if os.path.exists(raw_input_path):
            try:
                os.remove(raw_input_path)
                logger.info(f"已清理临时文件: {raw_input_path}")
            except Exception as e:
                logger.error(f"清理临时文件失败: {e}")
        if not enable_frame_enhancement:
            # 构建新文件名
            base_name, ext = os.path.splitext(input_path)
            new_filename = f"{base_name} {res_width}x{res_height} Viden2x_HQ{ext}"
            # 使用字符串截取方式获取原始目录路径
            original_dir = os.path.dirname(input_path)
            target_path = os.path.join(original_dir, new_filename)
            # 将输入文件移动到原目录
            try:
                if os.path.exists(output_path):
                    shutil.move(output_path, target_path)
                    logger.info(f"文件已移动到原目录: {target_path}")
                    file['处理步骤'] = 2.5  # 标记为只进行了增强
                    data_manager.update_record({"文件完整路径": file.get("文件完整路径")},file)
                else:
                    logger.error(f"输入文件不存在: {output_path}")
                    return
            except Exception as e:
                logger.error(f"移动文件到原目录失败: {e}")
                return

    except subprocess.CalledProcessError as e:
        logger.error(f"处理文件 {raw_input_path} 失败: {e}")
        # 清理临时文件
        if os.path.exists(raw_input_path):
            try:
                os.remove(raw_input_path)
                logger.info(f"已清理临时文件: {raw_input_path}")
            except Exception as e:
                logger.error(f"清理临时文件失败: {e}")
    except UnicodeDecodeError as e:
        logger.error(f"处理文件 {raw_input_path} 时发生编码错误: {e}")
        # 清理临时文件
        if os.path.exists(raw_input_path):
            try:
                os.remove(raw_input_path)
                logger.info(f"已清理临时文件: {raw_input_path}")
            except Exception as e:
                logger.error(f"清理临时文件失败: {e}")
    except Exception as e:
        logger.error(f"处理文件 {raw_input_path} 时发生未知错误: {e}")
        # 清理临时文件
        if os.path.exists(raw_input_path):
            try:
                os.remove(raw_input_path)
                logger.info(f"已清理临时文件: {raw_input_path}")
            except Exception as e:
                logger.error(f"清理临时文件失败: {e}")

def process_frame_enhancement(file, tmp_dir, frame_multiplier, frame_processor, rife_model, video2x_path, res_width, res_height, frame_encoder, frame_preset, frame_crf, threads, logger):
    """进行帧率增强处理"""
    input_filename = os.path.basename(file['文件完整路径'])
    input_path = os.path.join(tmp_dir, input_filename)
    
    # 验证临时文件路径是否存在
    if not os.path.exists(input_path):
        logger.warning(f"临时文件不存在，跳过帧率增强: {input_path}")
        return
    
    # 检查临时文件夹中是否存在已增强的文件
    if not os.path.exists(input_path):
        logger.warning(f"临时文件不存在，跳过帧率增强: {input_path}")
        return
    
    # 构建新文件名
    base_name, ext = os.path.splitext(input_filename)
    new_filename = f"{base_name} {res_width}x{res_height} fpsx{frame_multiplier} Viden2x_HQ{ext}"
    if not enable_resolution_enhancement:
        new_filename = f"{base_name} fpsx{frame_multiplier} Viden2x_HQ{ext}"
    output_path = os.path.join(tmp_dir, new_filename)
    
    # 设置环境变量
    env = os.environ.copy()
    env['CUDA_VISIBLE_DEVICES'] = '0'
    env['NVIDIA_VISIBLE_DEVICES'] = 'all'
    
    try:
        logger.info(f"开始帧率增强: {input_path}")
        start_time = time.time()
        cmd = [
            video2x_path,
            'upscale',  # 显式指定upscale命令
            '-i', input_path,
            '-o', output_path,
            '-m', str(frame_multiplier),
            '-p', frame_processor,
            '--rife-model', rife_model,
            '-c', frame_encoder,
            '-e', f'preset={frame_preset}',
            '-e', f'qp={frame_crf}',
            '-t', threads
        ]
        
        # 使用完整的命令字符串并确保路径正确引用
        cmd_str = f'"{video2x_path}" upscale -i "{input_path}" -o "{output_path}" -m {frame_multiplier} -p {frame_processor} --rife-model {rife_model} -c {frame_encoder} -e preset={frame_preset} -e qp={frame_crf} -t {threads}'

        # 为了在控制台显示输出，我们不捕获输出，但需要处理可能的异常
        try:
            result = subprocess.run(cmd_str, shell=True, capture_output=False, text=True, encoding='utf-8', errors='ignore', env=env)
        except Exception as e:
            logger.error(f"执行命令时发生异常: {e}")
            raise
        
        
        # 检查命令执行结果
        if result.returncode == 0:
            # 命令成功执行
            end_time = time.time()
            duration = end_time - start_time
            logger.info(f"帧率增强完成:{output_path},耗时: {duration:.2f}秒")
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
                    #对数据进行更新
                    data_manager.update_record({"文件完整路径": file.get("文件完整路径")},file)
                    # 清理临时画面增强文件
                    if os.path.exists(input_path):
                        os.remove(input_path)
                        logger.info(f"已清理临时画面增强文件: {input_path}")
                except Exception as e:
                    logger.error(f"文件移动或清理失败: {str(e)}")
            else:
                logger.error(f"帧率增强文件验证失败: {output_path} 不存在或为空")
        else:
            # 命令返回非零退出码，记录错误
            logger.error(f"帧率增强失败: 退出代码 {result.returncode}, 命令: {result.args}")
            # 对于特定的内存访问错误，标记为已完成处理以跳过
            if result.returncode == 3221225477:
                logger.info("检测到内存访问冲突错误，跳过此文件的帧率增强处理")
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
                        file['处理步骤'] = 3    #标记为已执行完全部处理
                        #对数据进行更新
                        data_manager.update_record({"文件完整路径": file.get("文件完整路径")},file)
                        # 清理临时画面增强文件
                        if os.path.exists(input_path):
                            os.remove(input_path)
                            logger.info(f"已清理临时画面增强文件: {input_path}")
                    except Exception as e:
                        logger.error(f"文件移动或清理失败: {str(e)}")
                elif os.path.exists(input_path):
                    # 如果帧率增强失败，但画面增强成功，将画面增强文件重命名并移动
                    logger.info("帧率增强失败，但画面增强成功，将使用画面增强文件")
                    original_dir = os.path.dirname(file['文件完整路径'])
                    base_name, ext = os.path.splitext(new_filename)
                    # 移除fpsx2部分
                    final_filename = f"{base_name.replace(f' {res_width}x{res_height} fpsx{frame_multiplier}', f' {res_width}x{res_height}')}{ext}"
                    target_path = os.path.join(original_dir, final_filename)
                    try:
                        os.makedirs(original_dir, exist_ok=True)
                        shutil.move(input_path, target_path)
                        logger.info(f"画面增强文件已移动至: {target_path}")
                    except Exception as e:
                        logger.error(f"文件移动失败: {str(e)}")
    except Exception as e:
        logger.error(f"帧率增强发生异常: {str(e)}")

def save_data(output_json_path, file_data_list, logger):
    """保存处理结果到JSON文件"""
    try:
        with open(output_json_path, 'w', encoding='utf-8') as f:
            json.dump(file_data_list, f, ensure_ascii=False, indent=2)
        logger.info("💾 结果已保存到: %s", output_json_path)
    except Exception as e:
        logger.error(f"保存数据时发生错误: {e}")

def setup_logger():
    """设置日志记录器"""
    # 创建日志目录（如果不存在）
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'log')
    os.makedirs(log_dir, exist_ok=True)
    
    # 创建日志文件路径
    log_file = os.path.join(log_dir, 'video_processor.log')
    
    # 创建日志记录器
    logger = logging.getLogger('video_processor')
    logger.setLevel(logging.INFO)
    
    # 避免重复添加处理器
    if not logger.handlers:
        # 创建文件处理器
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        
        # 创建控制台处理器
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        
        # 创建格式化器
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        # 添加处理器到日志记录器
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
    
    return logger

def process_file(file, tmp_dir, video2x_path, res_width, res_height, res_processor, res_shader, res_encoder, res_preset, res_crf, frame_multiplier, frame_processor, rife_model, frame_encoder, frame_preset, frame_crf, threads, logger):
    """处理单个文件，先执行画面增强，再执行帧率增强"""
    # 先执行画面增强
    if enable_resolution_enhancement or enable_frame_enhancement:
        if file.get("处理步骤") == 1 and enable_resolution_enhancement:
            process_single_file(file, tmp_dir, video2x_path, res_width, res_height, res_processor, res_shader, res_encoder, res_preset, res_crf, logger)
        elif file.get("处理步骤") == 1 and not enable_resolution_enhancement:
            # 如果不启用画面增强，直接跳到下一步，但需要先将源文件复制到tmp目录
            input_path = file["文件完整路径"]
            filename = os.path.basename(input_path)
            output_path = os.path.join(tmp_dir, filename)
            
            # 将输入文件复制到tmp目录
            try:
                if os.path.exists(input_path):
                    shutil.copy2(input_path, output_path)
                    logger.info(f"文件已复制到临时目录: {output_path}")
                else:
                    logger.error(f"输入文件不存在: {input_path}")
                    return
            except Exception as e:
                logger.error(f"复制文件到临时目录失败: {e}")
                return
                
            file["处理步骤"] = 2
            data_manager.update_record({"文件完整路径": file.get("文件完整路径")},file)
        # 再执行帧率增强
        if file.get("处理步骤") == 2 and enable_frame_enhancement:
            process_frame_enhancement(file, tmp_dir, frame_multiplier, frame_processor, rife_model, video2x_path, res_width, res_height, frame_encoder, frame_preset, frame_crf, threads, logger)
    else:
        logger.info("未启用画面增强和帧率增强，直接跳过处理")
        return


def video_processorn(file_info, tmp_dir, video2x_path, res_width, res_height, res_processor, res_shader, res_encoder, res_preset, res_crf, frame_multiplier, frame_processor, rife_model, frame_encoder, frame_preset, frame_crf, threads):
    """主函数，用于处理单个文件"""
    # 设置日志记录器
    logger = setup_logger()
    
    # 处理文件
    file_name = file_info.get('文件名带扩展名', '未知文件')
    try:  # 处理文件
        process_file(file_info, tmp_dir, video2x_path, res_width, res_height, res_processor, res_shader, res_encoder, res_preset, res_crf, frame_multiplier, frame_processor, rife_model, frame_encoder, frame_preset, frame_crf, threads, logger)
        logger.info(f"文件 '{file_name}' 处理完成")
        return True  # 处理成功
    except Exception as e:
        logger.error(f"处理文件 '{file_name}' 时发生错误: {e}")
        return False  # 处理失败