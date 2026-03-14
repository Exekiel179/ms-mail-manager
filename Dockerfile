# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
# Since we don't have a requirements.txt yet, we'll install manually or create it
RUN pip install --no-cache-dir fastapi uvicorn requests

# Copy the current directory contents into the container at /app
COPY . /app

# Expose port 8000 for the FastAPI server
EXPOSE 8000

# Run main.py when the container launches
CMD ["python", "backend/main.py"]
