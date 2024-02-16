# Use an official Python runtime as a parent image
FROM python:3.10

# Set the working directory in the container to /app
WORKDIR /app

# Add the current directory contents into the container at /app
ADD . /app

# Install requirements.txt
RUN pip install -r requirements.txt

# Install PostgreSQL
RUN apt-get update && apt-get install -y postgresql postgresql-contrib

# create database
RUN service postgresql start && createdb gadi
