import boto3
import json
import urllib.request
import os
import time

ec2 = boto3.client('ec2', region_name='ap-northeast-2')
ssm = boto3.client('ssm', region_name='ap-northeast-2')

INSTANCE_ID = os.environ['INSTANCE_ID']  # sanitized for public repo

def send_message(chat_id, text):
    token = os.environ['TELEGRAM_TOKEN']
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    data = json.dumps({'chat_id': chat_id, 'text': text}).encode()
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(req)

def get_ec2_state():
    desc = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
    return desc['Reservations'][0]['Instances'][0]['State']['Name']

def lambda_handler(event, context):
    body = json.loads(event.get('body', '{}'))
    message = body.get('message', {})
    chat_id = str(message.get('chat', {}).get('id', ''))
    text = message.get('text', '')

    allowed_id = os.environ['ALLOWED_CHAT_ID']
    if chat_id != allowed_id:
        return {'statusCode': 200, 'body': 'ok'}

    if text == '/start_ec2':
        state = get_ec2_state()
        if state == 'running':
            send_message(chat_id, '⚠️ 이미 실행 중이야')
        elif state == 'stopped':
            send_message(chat_id, '🔄 EC2 시작 중...')
            ec2.start_instances(InstanceIds=[INSTANCE_ID])
            waiter = ec2.get_waiter('instance_running')
            waiter.wait(InstanceIds=[INSTANCE_ID])
            time.sleep(20)
            ssm.send_command(
                InstanceIds=[INSTANCE_ID],
                DocumentName='AWS-RunShellScript',
                Parameters={
                    'commands': [
                        'pgrep -f "claude" && exit 0',
                        "sudo -u ubuntu tmux new-session -d -s claude '/home/ubuntu/start-claude-telegram.sh'"
                    ],
                    'executionTimeout': ['60']
                }
            )
            send_message(chat_id, '✅ EC2 켜졌어! Claude Code 실행 중...')
        else:
            send_message(chat_id, f'⚠️ 현재 상태: {state}')

    elif text == '/stop_ec2':
        state = get_ec2_state()
        if state == 'stopped':
            send_message(chat_id, '⚠️ 이미 꺼져있어')
        elif state == 'running':
            send_message(chat_id, '🔄 종료 중...')
            try:
                ssm.send_command(
                    InstanceIds=[INSTANCE_ID],
                    DocumentName='AWS-RunShellScript',
                    Parameters={
                        'commands': [
                            'pkill -f claude || true',
                            'sudo -u ubuntu tmux kill-server || true'
                        ],
                        'executionTimeout': ['10']
                    }
                )
                time.sleep(5)
            except:
                pass
            ec2.stop_instances(InstanceIds=[INSTANCE_ID])
            send_message(chat_id, '✅ EC2 종료됐어')
        else:
            send_message(chat_id, f'⚠️ 현재 상태: {state}')

    elif text == '/status':
        state = get_ec2_state()
        emoji = {'running': '🟢', 'stopped': '🔴', 'pending': '🟡', 'stopping': '🟡'}.get(state, '⚪')
        send_message(chat_id, f'{emoji} EC2 상태: {state}')

    elif text == '/logs':
        state = get_ec2_state()
        if state != 'running':
            send_message(chat_id, f'⚠️ EC2가 꺼져있어 ({state})')
        else:
            try:
                cmd = ssm.send_command(
                    InstanceIds=[INSTANCE_ID],
                    DocumentName='AWS-RunShellScript',
                    Parameters={
                        'commands': [
                            'sudo -u ubuntu tmux capture-pane -t claude -p | tail -30'
                        ],
                        'executionTimeout': ['10']
                    }
                )
                command_id = cmd['Command']['CommandId']
                time.sleep(3)
                result = ssm.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=INSTANCE_ID
                )
                output = result.get('StandardOutputContent', '').strip()
                if output:
                    send_message(chat_id, f'📋 터미널 로그:\n\n{output[:3500]}')
                else:
                    send_message(chat_id, '⚠️ 로그 없음 (tmux 세션 확인 필요)')
            except Exception as e:
                send_message(chat_id, f'❌ 로그 조회 실패: {str(e)}')

    elif text.startswith('/cmd '):
        state = get_ec2_state()
        if state != 'running':
            send_message(chat_id, f'⚠️ EC2가 꺼져있어 ({state})')
        else:
            user_input = text[5:]
            escaped_input = user_input.replace("'", "'\\''")
            try:
                cmd = ssm.send_command(
                    InstanceIds=[INSTANCE_ID],
                    DocumentName='AWS-RunShellScript',
                    Parameters={
                        'commands': [
                            f"sudo -u ubuntu tmux send-keys -t claude '{escaped_input}' Enter",
                            'sleep 4',
                            'sudo -u ubuntu tmux capture-pane -t claude -p | tail -20'
                        ],
                        'executionTimeout': ['15']
                    }
                )
                command_id = cmd['Command']['CommandId']
                time.sleep(6)
                result = ssm.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=INSTANCE_ID
                )
                output = result.get('StandardOutputContent', '').strip()
                if output:
                    send_message(chat_id, f'⌨️ 입력: `{user_input}`\n\n📋 결과:\n{output[:3500]}')
                else:
                    send_message(chat_id, f'✅ 입력 전송됨: `{user_input}`\n\n로그가 비어있어, `/logs`로 다시 확인해봐')
            except Exception as e:
                send_message(chat_id, f'❌ 실패: {str(e)}')

    return {'statusCode': 200, 'body': 'ok'}