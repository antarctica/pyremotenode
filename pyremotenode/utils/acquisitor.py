"""
    Module to produce common acquisition routines for event handlers in the schedule, example
    being to process the result of a command which will then be acted upon

    I think these should really be mixins, to avoid interfering with the behavioural of the main
    class hierarchy which defines the composition of the event objects. These items only ADD functionality
    to the main class structure, they aren't for composition
"""