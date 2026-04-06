from app import app

# Vercel entrypoint
def app_handler(environ, start_response):
    return app(environ, start_response)
