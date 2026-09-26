# ADR-0001: Autonomous Capability Acquisition

Aurora uses a brokered capability model. Agents request named capabilities; the broker evaluates policy before acquisition or execution. This preserves autonomous operation while preventing arbitrary package/source execution.