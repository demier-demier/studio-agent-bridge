"""Use as run_script on a workspace copy of the generated sensor enclosure."""


def build(app, design, run_dir):
    parameter = design.userParameters.itemByName("lens_length")
    if not parameter:
        raise ValueError("This example expects the parametric sensor enclosure demo")
    parameter.expression = "24 mm"
    if not design.computeAll():
        raise RuntimeError("Fusion could not recompute the design")
