"""本机模型配置自检，只返回受控状态，不访问API或返回密钥。"""
import os


# 提前发现缺失或明显错误的环境变量；实际鉴权和服务可用性仍由调用确认。
def model_configuration_issue():
    key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not key.strip():
        return '未配置模型服务密钥，请联系维护人员配置后重启服务。'
    if any(c.isspace() for c in key) or key.lower() in ('replace-with-your-key', 'your-api-key', '你的密钥'):
        return '模型服务密钥配置格式有误，请联系维护人员检查后重启服务。'
    model = os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash')
    if not model.strip() or any(c.isspace() for c in model):
        return '模型名称配置格式有误，请联系维护人员检查后重启服务。'
    return None
