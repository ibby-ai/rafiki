# Testing if we can read this file

def hello(message: str = "hello world"):
    return f"you said {message}"


my_message = hello("hello world")

if __name__ == "__main__":
    print(my_message)

