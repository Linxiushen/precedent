FROM ubuntu:24.04

# The two bundles the acceptor has to choose between.  Nothing is executed:
# this image exists so the files have a path, and the verifier needs only the
# shell that is already here.
WORKDIR /app
COPY incumbent/ /app/incumbent/
COPY candidate/ /app/candidate/
