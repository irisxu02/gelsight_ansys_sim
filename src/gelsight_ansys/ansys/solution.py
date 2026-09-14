"""Solution and contact-element controls shared by every MAPDL adapter.

Each accepted solver or contact setting is turned into its command here, once,
so a control that the configuration declares cannot be read by one adapter and
silently ignored by another.
"""

# ANSYS's own CUTCONTROL values. They are written explicitly, so a restart that
# returns a control to its default restates it rather than leaving whatever the
# resumed database last held.
DEFAULT_TRANSIENT_POINTS_PER_CYCLE = 13


def equation_solver_command(solver):
    """EQSLV from the declared equation solver; MAPDL names it in upper case."""
    return f"EQSLV,{solver.equation_solver.upper()}"


def cutback_commands(solver):
    points = (
        DEFAULT_TRANSIENT_POINTS_PER_CYCLE
        if solver.transient_points_per_cycle is None
        else solver.transient_points_per_cycle
    )
    return [
        f"CUTCONTROL,NPOINT,{points}",
        f"CUTCONTROL,NOITERPREDICT,{0 if solver.predict_cutback else 1}",
    ]


def diagnostics_commands(solver):
    if solver.nonlinear_diagnostics:
        # Identify the elements behind a distortion or penetration abort; the
        # streamed solver text reports "Element 0" once numbers are stripped.
        return ["NLDIAG,NRRE,ON", "NLDIAG,CONT,ITER"]
    return ["NLDIAG,NRRE,OFF", "NLDIAG,CONT,OFF"]


def solution_control_commands(solver, *, predictor=True):
    """Newton-Raphson controls that a restart must restate to take effect.

    A resumed model is rebuilt from gel.rdb, which carries the settings written
    when it was first built, so these are reissued rather than assumed. The
    equation solver is not among them: it is fixed when the model is built.
    """
    commands = [
        "NROPT,UNSYM" if solver.newton_raphson == "unsymmetric" else "NROPT,FULL",
        "AUTOTS,ON",
        "LNSRCH,ON",
    ]
    if not predictor:
        commands.append("PRED,OFF")
    commands += [
        f"NEQIT,{solver.iterations}",
        f"CNVTOL,F,,{solver.force_tolerance:.16g},{solver.force_norm},1e-6",
    ]
    return commands + cutback_commands(solver) + diagnostics_commands(solver)


def contact_keyopt_commands(indenter, element_type):
    """CONTA174 key options from the declared contact settings.

    KEYOPT(4) detection at Gauss points, (11) shell thickness off and (18)
    sliding behaviour are held at their defaults; the three that a setup can
    reasonably need to change are read from it.
    """
    from ..config import CONTACT_FORMULATIONS, CONTACT_SEPARATION

    return [
        f"KEYOPT,{element_type},2,{CONTACT_FORMULATIONS[indenter.contact_formulation]}",
        f"KEYOPT,{element_type},4,0",
        f"KEYOPT,{element_type},10,{2 if indenter.update_stiffness_each_iteration else 0}",
        f"KEYOPT,{element_type},11,0",
        f"KEYOPT,{element_type},12,{CONTACT_SEPARATION[indenter.contact_separation]}",
        f"KEYOPT,{element_type},18,0",
    ]
