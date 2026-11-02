"""仅根据客户描述生成固定预防提醒，不诊断故障或确认实物情况。"""

RISK_TERMS = ('渗水', '漏水', '漏液', '水渗出', '冒烟', '焦味', '异味', '异常发热',
              '外壳破损', '裂纹', '外壳裂', '玻璃碎', '玻璃破', '破裂', '漏电', '触电', '火花', '烧焦')
SAFETY_NOTICE = '为安全起见，请先停止使用，不要通电或自行拆机；请客服接手核查，具体处理方式需核实型号资料。'


# 从客户文本识别已覆盖的风险词，供成功回复及模型不可用时共用。
def customer_safety_notice(case):
    for message in case.get('messages', []):
        text = message.get('content', '')
        if message.get('role') == 'customer' and isinstance(text, str) and any(term in text for term in RISK_TERMS):
            return SAFETY_NOTICE
    return ''
