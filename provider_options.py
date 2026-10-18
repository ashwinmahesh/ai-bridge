"""Validate provider selections and translate them to native CLI argv safely."""
import json

EFFORTS = {
    'chatgpt': ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'),
    'gemini': ('low', 'medium', 'high', 'xhigh', 'max'),
    'claude': ('low', 'medium', 'high', 'xhigh', 'max', 'ultracode'),
}


def validate_options(recipient, model=None, effort=None):
    if recipient not in EFFORTS:
        raise ValueError('Unknown recipient')
    if model is not None and (not isinstance(model, str) or not model or len(model) > 200
                              or model.startswith('-') or not model.isprintable() or any(c.isspace() for c in model)):
        raise ValueError('model must be a nonempty model ID/alias without whitespace (up to 200 characters)')
    if effort is not None and (not isinstance(effort, str) or effort not in EFFORTS[recipient]):
        raise ValueError(f'Invalid effort for {recipient}; choose one of: {", ".join(EFFORTS[recipient])}')


def _strip_overrides(command, model, effort, codex):
    result, index = [], 0
    while index < len(command):
        arg = command[index]
        if model is not None and arg in ('--model', '-m') or effort is not None and not codex and arg == '--effort':
            index += 2
            continue
        if model is not None and arg.startswith('--model=') or effort is not None and not codex and arg.startswith('--effort='):
            index += 1
            continue
        if codex and arg in ('-c', '--config') and index + 1 < len(command):
            key = command[index + 1].split('=', 1)[0].strip()
            if key == 'model' and model is not None or key == 'model_reasoning_effort' and effort is not None:
                index += 2
                continue
        if codex and arg.startswith('--config='):
            key = arg[len('--config='):].split('=', 1)[0].strip()
            if key == 'model' and model is not None or key == 'model_reasoning_effort' and effort is not None:
                index += 1
                continue
        result.append(arg)
        index += 1
    return result


def command_options(recipient, agent, model=None, effort=None):
    """Message selection > bridge agent defaults > command flags > native defaults."""
    model = model if model is not None else agent.get('model')
    effort = effort if effort is not None else agent.get('effort')
    validate_options(recipient, model, effort)
    command = agent.get('command')
    if not isinstance(command, list) or not command or not all(isinstance(value, str) for value in command):
        raise ValueError('command must be an argv array')
    command = _strip_overrides(command, model, effort, recipient == 'chatgpt')
    additions = []
    if model is not None:
        additions.extend(['--model', model])
    if effort is not None:
        if recipient == 'chatgpt':
            additions.extend(['-c', 'model_reasoning_effort=' + json.dumps(effort)])
        else:
            additions.extend(['--effort', effort])
    # Preserve the terminal stdin sentinel used by Codex exec.
    if command[-1] == '-':
        command = command[:-1] + additions + ['-']
    else:
        command += additions
    # Resolve command-embedded settings too, so logs identify effective explicit choices.
    for index, arg in enumerate(command):
        if arg in ('--model', '-m') and index + 1 < len(command):
            model = command[index + 1]
        elif arg.startswith('--model='):
            model = arg.split('=', 1)[1]
        elif arg == '--effort' and index + 1 < len(command):
            effort = command[index + 1]
        elif arg.startswith('--effort='):
            effort = arg.split('=', 1)[1]
        elif recipient == 'chatgpt':
            value = command[index + 1] if arg in ('-c', '--config') and index + 1 < len(command) else arg[len('--config='):] if arg.startswith('--config=') else ''
            key, separator, selected = value.partition('=')
            if separator and key.strip() in ('model', 'model_reasoning_effort'):
                try:
                    selected = json.loads(selected.strip())
                except ValueError:
                    selected = selected.strip().strip("'")
                if key.strip() == 'model':
                    model = selected
                else:
                    effort = selected
    return command, model, effort
