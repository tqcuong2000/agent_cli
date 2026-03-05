import re
import os
from pathlib import Path

ROOT = Path('x:/agent_cli/agent_cli')
ASSETS_DIR = ROOT / 'assets'
APP_TCSS = ASSETS_DIR / 'app.tcss'

app_py = ROOT / 'core/ux/tui/app.py'

widgets = [
    ROOT / 'core/ux/tui/views/header/title.py',
    ROOT / 'core/ux/tui/views/header/terminal.py',
    ROOT / 'core/ux/tui/views/header/agent_badge.py',
    ROOT / 'core/ux/tui/views/header/header.py',
    ROOT / 'core/ux/tui/views/header/status.py',
    ROOT / 'core/ux/tui/views/footer/footer.py',
    ROOT / 'core/ux/tui/views/footer/user_input.py',
    ROOT / 'core/ux/tui/views/footer/submit_btn.py',
    ROOT / 'core/ux/tui/views/footer/user_interaction.py',
    ROOT / 'core/ux/tui/views/body/body.py',
    ROOT / 'core/ux/tui/views/body/text_window.py',
    ROOT / 'core/ux/tui/views/body/panel_window.py',
    ROOT / 'core/ux/tui/views/body/messages/user_message.py',
    ROOT / 'core/ux/tui/views/body/messages/system_message.py',
    ROOT / 'core/ux/tui/views/body/messages/agent_response.py',
    ROOT / 'core/ux/tui/views/body/messages/answer_block.py',
    ROOT / 'core/ux/tui/views/body/messages/thinking_block.py',
    ROOT / 'core/ux/tui/views/body/messages/tool_step.py',
    ROOT / 'core/ux/tui/views/body/messages/changed_file_detail_block.py',
    ROOT / 'core/ux/tui/views/body/panel/context_container.py',
    ROOT / 'core/ux/tui/views/body/panel/changed_file.py',
    ROOT / 'core/ux/tui/views/common/kv_line.py',
    ROOT / 'core/ux/tui/views/common/popup_list.py',
    ROOT / 'core/ux/tui/views/common/command_popup.py',
    ROOT / 'core/ux/tui/views/common/file_popup.py',
    ROOT / 'core/ux/tui/views/common/error_popup.py',
    ROOT / 'core/ux/tui/views/common/session_overlay.py',
]

css_blocks = []

print(f"Processing {app_py.name}")
content = app_py.read_text(encoding='utf-8')
match = re.search(r'    CSS = \"\"\"(.*?)\"\"\"', content, re.DOTALL)
if match:
    css_blocks.append('/* --- app.py --- */\n' + match.group(1).strip() + '\n')
    new_content = content[:match.start()] + '    CSS_PATH = "../../assets/app.tcss"' + content[match.end():]
    app_py.write_text(new_content, encoding='utf-8')
else:
    print('Could not find CSS in app.py')

for widget_py in widgets:
    print(f"Processing {widget_py.name}")
    if not widget_py.exists():
        print(f"  Not found: {widget_py}")
        continue
    content = widget_py.read_text(encoding='utf-8')
    match = re.search(r'    DEFAULT_CSS = \"\"\"(.*?)\"\"\"', content, re.DOTALL)
    if match:
        css_blocks.append(f"/* --- {widget_py.name} --- */\n" + match.group(1).strip() + "\n")
        new_content = content[:match.start()] + "    DEFAULT_CSS = \"\"" + content[match.end():]
        widget_py.write_text(new_content, encoding='utf-8')
    else:
        print(f"  Could not find DEFAULT_CSS in {widget_py.name}")

APP_TCSS.write_text('\n\n'.join(css_blocks), encoding='utf-8')
print("Consolidation complete.")
