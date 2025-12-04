import json
import os
import logging
from typing import List, Dict, Any, Optional

class DataManager:
    """用于对JSON数据进行增删改查操作的数据管理器"""
    def __init__(self, data_file_path: str):
        """
        初始化数据管理器
        Args:
            data_file_path: JSON数据文件的路径
        """
        self.data_file_path = data_file_path
        self.logger = logging.getLogger(__name__)
    def load_data(self) -> List[Dict[str, Any]]:
        """
        从JSON文件加载数据
        Returns:
            包含数据的列表
        """
        try:
            if os.path.exists(self.data_file_path):
                with open(self.data_file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data
            else:
                self.logger.warning(f"数据文件 {self.data_file_path} 不存在，返回空列表")
                return []
        except Exception as e:
            self.logger.error(f"加载数据时发生错误: {e}")
            return []
    
    def save_data(self, data: List[Dict[str, Any]]) -> bool:
        """
        将数据保存到JSON文件
        Args:
            data: 要保存的数据列表
        Returns:
            保存是否成功
        """
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.data_file_path), exist_ok=True)
            with open(self.data_file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.logger.info(f"数据已保存到: {self.data_file_path}")
            return True
        except Exception as e:
            self.logger.error(f"保存数据时发生错误: {e}")
            return False
    
    def add_record(self, record: Dict[str, Any]) -> bool:
        """
        添加一条新记录
        
        Args:
            record: 要添加的记录
            
        Returns:
            添加是否成功
        """
        try:
            data = self.load_data()
            data.append(record)
            return self.save_data(data)
        except Exception as e:
            self.logger.error(f"添加记录时发生错误: {e}")
            return False
    
    def delete_record(self, condition: Dict[str, Any]) -> int:
        """
        根据条件删除记录
        
        Args:
            condition: 删除条件，键值对形式
            
        Returns:
            删除的记录数量
        """
        try:
            data = self.load_data()
            original_length = len(data)
            
            # 过滤掉满足条件的记录
            data = [record for record in data if not all(record.get(k) == v for k, v in condition.items())]
            
            deleted_count = original_length - len(data)
            
            if deleted_count > 0:
                self.save_data(data)
                self.logger.info(f"成功删除 {deleted_count} 条记录")
            
            return deleted_count
        except Exception as e:
            self.logger.error(f"删除记录时发生错误: {e}")
            return 0
    
    def update_record(self, condition: Dict[str, Any], updates: Dict[str, Any]) -> int:
        """
        根据条件更新记录
        
        Args:
            condition: 更新条件，键值对形式
            updates: 要更新的字段和值
            
        Returns:
            更新的记录数量
        """
        try:
            data = self.load_data()
            updated_count = 0
            
            for record in data:
                # 检查是否满足更新条件
                if all(record.get(k) == v for k, v in condition.items()):
                    # 更新记录
                    for key, value in updates.items():
                        record[key] = value
                    updated_count += 1
            
            if updated_count > 0:
                self.save_data(data)
                self.logger.info(f"成功更新 {updated_count} 条记录")
            
            return updated_count
        except Exception as e:
            self.logger.error(f"更新记录时发生错误: {e}")
            return 0
    
    def query_records(self, condition: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        根据条件查询记录
        
        Args:
            condition: 查询条件，键值对形式，如果为None则返回所有记录
            
        Returns:
            符合条件的记录列表
        """
        try:
            data = self.load_data()
            
            if condition is None:
                return data
            # 过滤满足条件的记录
            result = [record for record in data if all(record.get(k) == v for k, v in condition.items())]
            return result
        except Exception as e:
            self.logger.error(f"查询记录时发生错误: {e}")
            return []

# 使用示例
if __name__ == "__main__":
    # 创建数据管理器实例
    dm = DataManager("data/example.json")
    
    # 添加记录
    new_record = {
        "name": "测试文件",
        "path": "C:/test/file.mp4",
        "size": 1024,
        "processed": False
    }
    dm.add_record(new_record)
    
    # 查询记录
    records = dm.query_records({"name": "测试文件"})
    print("查询结果:", records)
    
    # 更新记录
    dm.update_record({"name": "测试文件"}, {"processed": True})
    
    # 删除记录
    dm.delete_record({"name": "测试文件"})