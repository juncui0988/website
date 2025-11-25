from flask import Flask

# Create an instance of the Flask class
# __name__ is the name of the current Python module
app = Flask(__name__)


# Define a route for the root URL ("/")
# This decorator tells Flask which URL should trigger our function
@app.route('/')
def hello_world():
    """This function runs when the root URL is accessed."""
    return 'Hello, World!'


# Check if the script is executed directly (not imported)
if __name__ == '__main__':
    # Run the app in debug mode, which provides helpful error messages
    # and automatically reloads the server when code changes.
    app.run(debug=True)
