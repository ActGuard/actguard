def patch_all() -> None:
    from .openai import patch_openai
    from .anthropic import patch_anthropic
    from .google import patch_google

    patch_openai()
    patch_anthropic()
    patch_google()
