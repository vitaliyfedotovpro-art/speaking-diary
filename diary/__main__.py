from .server import serve
import sys
serve(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8791)
