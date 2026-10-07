from typing import Optional, Dict, Annotated
import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# 本文件作为 MCP stdio 子进程独立启动，需自行加载 .env（密钥不入 Git）
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

mcp = FastMCP(log_level="WARNING")

# 查到的表结构优化
class Response(BaseModel):
    success: bool               # 响应是否成功
    database: str             # 数据库名
    table: str                # 表名
    data: Optional[dict] | Optional[list]               # 返回数据
    rowcount: Optional[int] = None           # 影响行数

# 连接参数全部来自环境变量（.env，已被 .gitignore 排除），密码必填、其余给默认值
MYSQL_CONFIG = {
    'host': os.environ.get('MYSQL_HOST', 'localhost'),
    'port': int(os.environ.get('MYSQL_PORT', '3308')),
    'user': os.environ.get('MYSQL_USER', 'root'),
    'password': os.environ.get('MYSQL_PASSWORD', ''),
    'charset': 'utf8mb4',
}
if not MYSQL_CONFIG['password']:
    raise RuntimeError("缺少 MYSQL_PASSWORD 环境变量，请在项目根目录 .env 中配置")

# 连接沙盒中的MySQL
def get_connection(db=None):
    config = MYSQL_CONFIG.copy()
    if db:
        config['database'] = db
    # 连接失败直接抛出真实异常，并附明确 host:port——
    # 禁止返回字符串哨兵，否则上层 result,rowcount 解包会掩盖真实原因（回归场景 33）；
    # pymysql 自带文本只含 host 不含 port，会让模型误判连接端口
    try:
        return pymysql.connect(**config)
    except Exception as e:
        target = f"{config['host']}:{config['port']}"
        if db:
            target += f"/{db}"
        raise RuntimeError(f"无法连接 MySQL（目标 {target}）：{e}") from e

def execute_query(command, database=None, params = None, commit = False):
    connection = get_connection(database)
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(command, params)

            # 获取查询结果
            result = cursor.fetchall()
            # 遇到需要提交的情况时
            if commit:
                connection.commit()

            return result, cursor.rowcount
    finally:
        connection.close()


# 查看所连接中的数据库
@mcp.tool(name="mysql_list_databases", description="列举MySQL中包含哪些数据库")
def mysql_list_databases():
    try:
        result, rowcount = execute_query("show databases")
        if isinstance(result, str):
            return result
        databases = [row['Database'] for row in result]
        return Response(
            success=True,
            database="",
            table="",
            data=databases,
            rowcount=rowcount
        )
    except Exception as e:
        msg = f"数据库查询错误：" + str(e)
        return msg

# 查询该数据库中的表
@mcp.tool(name="mysql_list_tables", description="列举MySQL中某个数据库包含哪些表")
def mysql_list_tables(database: str):
    try:
        result, rowcount = execute_query(f"show tables from {database}")
        tables = [list(row.values())[0] for row in result]
        return Response(
            success=True,
            database=database,
            table="",
            data=tables,
            rowcount=rowcount
        )
    except Exception as e:
        msg = f"数据库中的表查询错误：" + str(e)
        return msg


# 获取数据库表的结构
@mcp.tool(name="mysql_describe_tables", description="列举MySQL中某个数据库某个表的结构")
def mysql_describe_tables(database: str, table: str):
    try:
        request, rowcount = execute_query(f"describe {table}", database=database)
        return Response(
            success=True,
            database=database,
            table=table,
            data=request,
            rowcount=rowcount
        )
    except Exception as e:
        msg = f"数据库表结构查询错误：" + str(e)
        return msg

# 查询数据库表中的数据
@mcp.tool(name="mysql_execute_query", description="执行SQL查询语句")
def mysql_execute_query(command, database=None, params: Optional[list] = None):
    try:
        params_tuple = tuple(params) if params else None
        result, rowcount = execute_query(command, database=database, params=params_tuple)
        return Response(
            success=True,
            database=database,
            table="",
            data=result,
            rowcount=rowcount
        )
    except Exception as e:
        msg = f"数据库查询错误：" + str(e)
        return msg


#  往数据库指定表中插入数据
@mcp.tool(name="mysql_insert_data", description="向MySQL中某个数据库某个表插入数据")
def mysql_insert_data(database: str, table: str, data: Dict[str, str]):
    columns = list(data.keys())
    values = list(data.values())
    values_wrapper = ','.join(['%s'] * len(values))
    command = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values_wrapper})"
    # print(command)
    try:
        result, rowcount = execute_query(command, database=database, params=tuple(values), commit=True)
        return Response(
            success=True,
            database=database,
            table=table,
            data=result,
            rowcount=rowcount
        )
    except Exception as e:
        msg = f"数据库插入错误：" + str(e)
        return msg


# 更新表中数据
@mcp.tool(name="mysql_update_data", description="更新MySQL中某个数据库某个表的数据")
def mysql_update_data(database: str, table: str, data: Dict[str, str], where: Dict[str, str]):
    set_clause = ','.join([f"{k}=%s" for k in data.keys()])         # set语句
    where_clause = " and ".join([f"{k}=%s" for k in where.keys()])
    command = f"UPDATE {table} SET {set_clause} WHERE {where_clause}"          # 更新语句

    set_params = list(data.values())
    where_params = list(where.values())
    params = set_params + where_params
    # print(command, params)
    try:
        result, rowcount = execute_query(command, database=database, params=tuple(params), commit=True)
        return Response(
            success=True,
            database=database,
            table=table,
            data=result,
            rowcount=rowcount
        )
    except Exception as e:
        msg = f"数据库更新错误：" + str(e)
        return msg

# 删除表中的数据
@mcp.tool(name="mysql_delete_data", description="删除MySQL中某个数据库某个表的数据")
def mysql_delete_data(database: str, table: str, where: Dict[str, str]):
    where_clause = " and ".join([f"{k}=%s" for k in where.keys()])
    command = f"DELETE FROM {table} WHERE {where_clause}"          # 更新语句


    params = list(where.values())
    try:
        result, rowcount = execute_query(command, database=database, params=tuple(params), commit=True)
        return Response(
            success=True,
            database=database,
            table=table,
            data=result,
            rowcount=rowcount
        )
    except Exception as e:
        msg = f"数据库删除错误：" + str(e)
        return msg


# 创建数据库
@mcp.tool(name="mysql_create_database", description="创建新的MySQL数据库")
def mysql_create_database(database_name: str, charset: str = "utf8mb4"):
    command = f"CREATE DATABASE {database_name} CHARACTER SET {charset}"
    try:
        result, rowcount = execute_query(command)
        return Response(
            success=True,
            database=database_name,
            table="",
            data=result,
            rowcount=rowcount
        )
    except Exception as e:
        msg = f"数据库创建错误：" + str(e)
        return msg


# 创建表
def mysql_create_table(
        database: str,
        table_name: str,
        table_columns: Annotated[str, Field(description="建表语句中的字段部分", json_schema_extra={"example": "id int(11) NOT NULL AUTO_INCREMENT, name varchar(255) COLLATE utf8mb4_general_ci NOT NULL, PRIMARY KEY (`id`)"})],
        table_schema: Annotated[str, Field(description="建表语句中的补充部分", json_schema_extra={"example": "ENGINE=InnoDB AUTO_INCREMENT=8 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"})]
):
    """
    建表语句实例：
    CREATE TABLE `user` (
        `id` int(11) NOT NULL AUTO_INCREMENT,
        `name` varchar(255) COLLATE utf8mb4_general_ci NOT NULL,
        PRIMARY KEY (`id`)
    ) ENGINE=InnoDB AUTO_INCREMENT=8 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
    :param database:
    :param table_name:
    :param table_columns:
    :param table_schema:
    :return:
    """
    command = f"CREATE TABLE {table_name} ({table_columns}) {table_schema}"
    try:
        result, rowcount = execute_query(command, database=database)
        return Response(
            success=True,
            database=database,
            table=table_name,
            data=result,
            rowcount=rowcount
        )
    except Exception as e:
        msg = f"数据库表创建错误：" + str(e)
        return msg


# 执行SQL命令
@mcp.tool(name="mysql_execute_command", description="执行特定的MySQL命令，如：变更表结构、增减字段")
def mysql_execute_command(command: str, database: str = None):
    try:
        result, rowcount = execute_query(command, database=database, commit=True)
        return Response(
            success=True,
            database=database,
            table="",
            data=result,
            rowcount=rowcount
        )
    except Exception as e:
        msg = f"数据库命令执行错误：" + str(e)
        return msg

if __name__ == "__main__":
    mcp.run(transport="stdio")
    # print(mysql_list_databases())
    # print(mysql_list_tables("test"))
    # print(mysql_describe_tables("test", "user"))
    # print(mysql_execute_query("select * from user", database="test"))
    # print(mysql_execute_query("select * from user where name=%s", database="test", params=["sam"]))
    # print(mysql_insert_data("test", "user", {"id": "3","name": "someone"}))
    # print(mysql_update_data("test", "user", {"name": "lucy"}, {"id": "3", "name": "someone"}))
    # print(mysql_delete_data("test", "user", {"id": "3"}))
    # print(mysql_create_database("test2"))