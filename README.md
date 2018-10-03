# Openshift Logs

See what's going on in your project or cluster.  Prints all your events and pod
updates.

- Install via `pip install .` (preferable in a virtualenv).
- Save your Openshift token in a file called `token`, or specify the file using `--token`
- Optionally specify the Openshift token via `--api-token`
- run the `ocl` command:

```
ocl --api <your openshift api> [-n <project]
```

or as follows if specifying the token directly via `--api-token`:

```
ocl --api <your openshift api> --api-token <your openshift api token> [-n <project]
```
