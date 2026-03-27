/**
 * Troubleshooting Decision Trees — AMC Support Chatbot
 * Zero tokens, zero API calls. Pure frontend logic.
 * Data verified against actual AMC PDF manuals.
 */
const TROUBLESHOOT_TREES = {

  // =========================================================================
  // TREE 1: RED LED / FAULT INDICATOR
  // Sources: HW Manual DigiFlex p.48, AppNote 009, ACE Manual p.138-139
  // =========================================================================
  red_led: {
    title: "Red LED / Fault Diagnosis",
    trigger_keywords: [
      "red led", "red light", "fault led", "status led red", "led is red",
      "led turns red", "red indicator", "fault indicator", "bridge disabled",
      "power bridge disabled", "drive faulted", "drive fault"
    ],
    root: "q_family",
    nodes: {
      q_family: {
        question: "What drive family are you working with?",
        options: [
          { label: "DigiFlex (DP/DZ/DX)", next: "q_digiflex_which_led" },
          { label: "FlexPro (FE/FM/FD/FX)", next: "q_flexpro_led" },
          { label: "AxCent (AZ)", next: "q_axcent_led" },
          { label: "Analog (B/S/100A/120A)", next: "q_analog_step1" },
          { label: "Not sure", next: "q_generic_red" },
        ]
      },

      // --- DigiFlex branch ---
      q_digiflex_which_led: {
        question: "Which LED is red on the DigiFlex drive?",
        options: [
          { label: "Power LED (red or flashing red/green)", next: "a_digiflex_power_red" },
          { label: "Status LED (solid red)", next: "q_digiflex_motor_connected" },
          { label: "Both LEDs are off", next: "a_no_power" },
          { label: "Not sure which LED", next: "a_digiflex_led_id" },
        ]
      },
      a_digiflex_power_red: {
        answer: "The **Power LED flashing red/green** indicates the **shunt regulator** is actively dissipating regenerative energy. This happens during motor deceleration when energy flows back into the DC bus.\n\n**This is often normal behavior** during deceleration. However, if it's constant:\n1. Check if the motor is being back-driven or the load is overhauling\n2. Verify the deceleration rate isn't too aggressive\n3. Consider adding an external shunt resistor for high-inertia loads\n4. Check that DC bus voltage stays within the drive's rated range",
        source: "AMC_HWManual_DigiFlex_Panel_EtherCAT.pdf p.48"
      },
      q_digiflex_motor_connected: {
        question: "Is the motor currently connected to the drive?",
        options: [
          { label: "Yes, motor is connected", next: "q_digiflex_halls" },
          { label: "No, motor is disconnected", next: "q_digiflex_inhibit" },
        ]
      },
      q_digiflex_inhibit: {
        question: "Check the hardware enable/inhibit inputs. Are all enable inputs in the ACTIVE state?",
        options: [
          { label: "Yes, all enable inputs are active", next: "q_digiflex_sto" },
          { label: "No / Not sure", next: "a_digiflex_enable" },
          { label: "I don't know where the enable pins are", next: "a_digiflex_enable_help" },
        ]
      },
      a_digiflex_enable: {
        answer: "The Status LED is red because the drive is **inhibited** (disabled via hardware input).\n\n**Fix:** Ensure all enable/inhibit pins are in the correct state. Check your drive's datasheet for the specific pin assignments. Common pins:\n- **INH (Inhibit)** — must be HIGH (+24V) to enable on most models\n- **ENA (Enable)** — must be HIGH to enable\n- Also check the **software enable** — send Controlword 0x06 → 0x07 → 0x0F via your network protocol",
        source: "AMC_HWManual_DigiFlex_Panel_EtherCAT.pdf p.48"
      },
      a_digiflex_enable_help: {
        answer: "Check your DigiFlex drive's **HW Installation Manual** for the I/O connector pinout. The enable/inhibit pins are on the main I/O connector.\n\n**Typical setup:**\n- Find the INH or ENA pin on the I/O connector\n- Apply +24V to enable the drive\n- Check both hardware AND software enable states\n- In DriveWare: check the Bridge Status indicator (green = enabled, red = disabled)",
        source: "AMC_HWManual_DigiFlex_Panel_EtherCAT.pdf"
      },
      q_digiflex_sto: {
        question: "Does your drive have STO (Safe Torque Off)? Check if there are STO-1 and STO-2 inputs.",
        options: [
          { label: "Yes, it has STO inputs", next: "a_digiflex_sto_fix" },
          { label: "No STO / Not applicable", next: "a_digiflex_check_faults" },
        ]
      },
      a_digiflex_sto_fix: {
        answer: "The drive may be disabled by **STO (Safe Torque Off)**.\n\n**Fix:** Both STO-1 AND STO-2 must have +24V applied to enable the drive. If either is LOW (0V), the drive will disable the output and show a red Status LED.\n\nCheck:\n1. Both STO inputs have +24V from your safety circuit\n2. The safety relay or E-stop chain is not tripped\n3. STO wiring is correct per the HW Installation Manual",
        source: "AMC_HWManual_DigiFlex_Panel_EtherCAT.pdf"
      },
      q_digiflex_halls: {
        question: "Slowly rotate the motor shaft by hand one full revolution. Does the Status LED stay red the entire time, or does it flash/change at certain positions?",
        options: [
          { label: "Stays red the entire revolution", next: "a_digiflex_check_faults" },
          { label: "Flashes or changes at certain positions", next: "a_digiflex_hall_fault" },
          { label: "Can't rotate the motor", next: "a_digiflex_check_faults" },
        ]
      },
      a_digiflex_hall_fault: {
        answer: "The LED changing at specific positions indicates an **invalid Hall sensor state**.\n\n**The Hall sensors are likely miswired or one is faulty.**\n\nValid Hall states cycle through 6 patterns (001→011→010→110→100→101). States **000** and **111** are invalid and cause a fault.\n\n**Fix:**\n1. Check Hall sensor wiring — verify +5V supply, ground, and all 3 signal connections\n2. Use an oscilloscope or DriveWare scope to monitor Hall states while rotating\n3. If one channel is stuck HIGH or LOW, that sensor may be damaged\n4. Try swapping Hall A/B/C connections if the motor runs backward or faults",
        source: "AMC_HWManual_AnalogDrives.pdf p.55, AMC_AppNote_009.pdf p.1"
      },
      a_digiflex_check_faults: {
        answer: "The drive has an active fault. **Connect via DriveWare** to read the specific fault code:\n\n1. Launch DriveWare and connect to the drive\n2. Check the **Bridge Status** panel — it will show the fault name\n3. Common faults:\n   - **Over-voltage** — DC bus too high (check supply, reduce decel rate)\n   - **Over-current** — short circuit or tuning issue (check motor wiring)\n   - **Over-temperature** — insufficient heatsinking (check thermal contact)\n   - **Hall State Error** — bad Hall sensors (check wiring)\n   - **Communication Error** — network timeout (check cables)\n4. Clear the fault: Controlword bit 7 (fault reset) or cycle power",
        source: "AMC_SW_Manual_ACE.pdf p.138-139"
      },
      a_digiflex_led_id: {
        answer: "DigiFlex drives have **two LEDs**:\n\n- **Power LED** (near the power connector) — shows power supply status\n- **Status LED** (near the I/O connector) — shows bridge enabled/disabled\n\nLook at which one is red:\n- Power LED red/green flash = shunt regulator (often normal during decel)\n- Status LED solid red = bridge disabled due to fault or inhibit",
        source: "AMC_HWManual_DigiFlex_Panel_EtherCAT.pdf p.48"
      },

      // --- FlexPro branch ---
      q_flexpro_led: {
        question: "What is the LED doing on your FlexPro drive?",
        options: [
          { label: "Solid red", next: "q_flexpro_fault" },
          { label: "Blinking red", next: "q_flexpro_fault" },
          { label: "Green blinking (not enabled)", next: "a_flexpro_not_enabled" },
          { label: "No LED at all", next: "a_no_power" },
        ]
      },
      q_flexpro_fault: {
        question: "Can you connect to the drive with ACE software?",
        options: [
          { label: "Yes, ACE connects", next: "a_flexpro_read_fault" },
          { label: "No, can't connect", next: "a_flexpro_no_connect" },
        ]
      },
      a_flexpro_read_fault: {
        answer: "**In ACE software**, check the fault:\n\n1. Go to the **Dashboard** tab — the fault name will be displayed\n2. Check **Safety Actions** panel for what triggered\n3. Common FlexPro faults:\n   - **Motor Over Temperature** — check motor thermistor wiring\n   - **Hall State Error** — check Hall sensor connections on P1 connector\n   - **Over-voltage** — DC bus exceeded limit (check supply voltage, add shunt)\n   - **Over-current** — check motor wiring for shorts\n   - **STO Active** — both STO-1 and STO-2 need +24V\n4. Clear fault: click **Fault Reset** in ACE or cycle power",
        source: "AMC_SW_Manual_ACE.pdf p.138-139"
      },
      a_flexpro_not_enabled: {
        answer: "**Green blinking** means the drive is powered but **not enabled**.\n\n**To enable:**\n1. Check hardware enable pin on P1 connector\n2. Send software enable via ACE (click Enable Bridge) or via network (Controlword 0x06 → 0x07 → 0x0F)\n3. Check STO inputs — both must be +24V\n4. Verify no fault is pending (check ACE Dashboard)",
        source: "AMC_HWManual_FlexPro_PCB.pdf"
      },
      a_flexpro_no_connect: {
        answer: "If you can't connect to the FlexPro via ACE:\n\n1. **USB cable** — use USB-A to USB-B, try a different cable\n2. **Address** — default is 63. Try scanning in ACE (Communication → Scan)\n3. **Power** — drive must be powered for USB communication\n4. **Driver** — check Windows Device Manager for the USB device. Try uninstalling and re-enumerating\n5. **Multiple drives** — if on RS-485 bus, each must have unique address\n\nOnce connected, read the fault code from the Dashboard.",
        source: "AMC_AppNote_050.pdf p.1"
      },

      // --- AxCent branch ---
      q_axcent_led: {
        question: "AxCent drives have a single status LED. Is it solid red or did it turn red during operation?",
        options: [
          { label: "Red on power-up (never turned green)", next: "a_axcent_powerup_fault" },
          { label: "Was green, turned red during operation", next: "q_axcent_during_operation" },
          { label: "Turns red when motor rotates", next: "a_axcent_hall_issue" },
        ]
      },
      a_axcent_powerup_fault: {
        answer: "Red LED immediately on power-up indicates:\n\n1. **Over-voltage** — DC supply is too high. Check with a multimeter, compare to drive's rated range on the datasheet\n2. **Under-voltage** — DC supply too low\n3. **Invalid Hall state** — Hall sensors reading 000 or 111 (check wiring)\n4. **Inhibit active** — enable inputs not in correct state\n5. **DIP switch misconfiguration** — verify DIP switches match your motor type and current mode\n\nCheck the DIP switches first — they're the most common cause on AxCent drives.",
        source: "AMC_HWManual_AxCent_Panel.pdf, AMC_AppNote_051.pdf"
      },
      q_axcent_during_operation: {
        question: "What was happening when it turned red?",
        options: [
          { label: "Motor was decelerating/stopping", next: "a_axcent_overvoltage" },
          { label: "Motor was under heavy load", next: "a_axcent_overcurrent" },
          { label: "Running for a long time", next: "a_axcent_overtemp" },
          { label: "Not sure / happened suddenly", next: "a_axcent_general" },
        ]
      },
      a_axcent_overvoltage: {
        answer: "Red LED during deceleration = **over-voltage from regeneration**.\n\nThe motor acts as a generator during deceleration, pumping voltage back into the DC bus.\n\n**Fix:**\n1. Reduce deceleration rate (ramp down more slowly)\n2. Add an external shunt resistor to dissipate regenerative energy\n3. Check if the drive model has a built-in shunt regulator (see datasheet)\n4. For vertical/gravity loads, a shunt resistor is usually required",
        source: "AMC_AppNote_018.pdf, AMC_HWManual_AxCent_Panel.pdf"
      },
      a_axcent_overcurrent: {
        answer: "Red LED under heavy load = **over-current fault**.\n\n**Fix:**\n1. Check that continuous current limit (DIP switches + potentiometer) is set correctly for your motor\n2. Verify the motor isn't mechanically stalled or overloaded\n3. Check motor wiring for shorts between phases — measure with multimeter\n4. If the motor needs more current, you may need a higher-rated drive\n5. Check current limit potentiometer setting",
        source: "AMC_HWManual_AxCent_Panel.pdf p.52, AMC_AppNote_016.pdf"
      },
      a_axcent_overtemp: {
        answer: "Red LED after extended operation = **over-temperature** (~65°C internal limit).\n\n**Fix:**\n1. Check heatsink thermal contact — thermal pad or compound must be applied\n2. Ensure adequate airflow around the drive\n3. Reduce continuous current if running near the drive's limit\n4. Check ambient temperature — derate at high temperatures\n5. The drive will auto-recover once temperature drops below threshold",
        source: "AMC_HWManual_AxCent_Panel.pdf"
      },
      a_axcent_general: {
        answer: "Check these common AxCent fault causes in order:\n\n1. **DIP switches** — verify they match your motor type (brushed/brushless) and desired current mode\n2. **Current limit pot** — set to appropriate level for your motor\n3. **DC bus voltage** — measure at the power terminals, verify it's within rated range\n4. **Motor wiring** — check for shorts between phases and to ground\n5. **Hall sensors** — rotate motor by hand, LED should stay green through full revolution\n6. **Enable inputs** — verify inhibit pin is in the correct state\n\nAxCent drives are analog — there are no software diagnostics. Troubleshooting is done with a multimeter and the datasheet.",
        source: "AMC_HWManual_AxCent_Panel.pdf, AMC_AppNote_051.pdf"
      },
      a_axcent_hall_issue: {
        answer: "Red LED at specific motor positions = **invalid Hall sensor state**.\n\nValid Hall states cycle: 001→011→010→110→100→101. States 000 and 111 = fault.\n\n**Fix:**\n1. Check all 3 Hall sensor signal wires are connected\n2. Verify +5V Hall supply is present\n3. Check for damaged cables or intermittent connections\n4. If one Hall is dead, the LED will go red at 2 positions per revolution\n5. Swap Hall A/B/C if motor runs in wrong direction",
        source: "AMC_HWManual_AnalogDrives.pdf p.55"
      },

      // --- Analog branch (9-step procedure from AppNote 009) ---
      q_analog_step1: {
        question: "Let's follow the App Note 009 isolation procedure. First: **remove ALL connections** from the drive (motor, feedback, controller I/O). Leave only DC power. Is the LED now green?",
        options: [
          { label: "Yes, LED turned green", next: "q_analog_step2" },
          { label: "No, still red with only power", next: "a_analog_power_fault" },
        ]
      },
      a_analog_power_fault: {
        answer: "LED is red with only power connected. This means:\n\n1. **Over-voltage** — measure DC bus voltage. Must be within the drive's rated range (check datasheet)\n2. **Under-voltage** — supply too low for the drive model\n3. **Internal fault** — the drive may be damaged\n4. **DIP switch error** — verify configuration matches your intended setup\n\nIf voltage is correct and DIP switches are right, the drive may need repair/replacement.",
        source: "AMC_AppNote_009.pdf p.1"
      },
      q_analog_step2: {
        question: "Good — the drive is OK with power only. Now **connect the Hall sensors** (or encoder). Slowly rotate the motor shaft by hand. Does the LED stay green?",
        options: [
          { label: "Yes, stays green during rotation", next: "q_analog_step3" },
          { label: "No, turns red at certain positions", next: "a_analog_hall_fault" },
          { label: "Turns red immediately when connected", next: "a_analog_feedback_short" },
        ]
      },
      a_analog_hall_fault: {
        answer: "**Hall sensor issue detected.** The LED turns red at positions where the Hall state is invalid (000 or 111).\n\n**Fix:**\n1. Check all 3 Hall wires are connected and not broken\n2. Verify +5V supply to Hall sensors\n3. Check for damaged/crushed cables\n4. One dead Hall channel will cause faults at 2 positions per revolution\n5. Try swapping Hall A, B, C to test each channel individually",
        source: "AMC_AppNote_009.pdf p.1, AMC_HWManual_AnalogDrives.pdf p.55"
      },
      a_analog_feedback_short: {
        answer: "Red LED immediately on connecting feedback = **wiring short**.\n\nCheck:\n1. Feedback cable shield is only grounded at ONE end (drive end)\n2. No pinched or damaged wires in the feedback cable\n3. Signal wires are not shorted to shield or each other\n4. Connector pins are properly crimped/soldered",
        source: "AMC_AppNote_009.pdf p.1"
      },
      q_analog_step3: {
        question: "Hall sensors are good. Now **connect the motor power wires** (U/V/W or A/B). Does the LED stay green?",
        options: [
          { label: "Yes, stays green", next: "q_analog_step4" },
          { label: "No, turns red", next: "a_analog_motor_short" },
        ]
      },
      a_analog_motor_short: {
        answer: "Red LED when motor is connected = **short circuit in motor wiring**.\n\n**Check with a multimeter:**\n1. Disconnect motor from drive\n2. Measure resistance between each pair of motor phases (U-V, V-W, U-W) — should be equal, typically 0.5-50 ohms\n3. Measure each phase to ground — should be >1 MΩ\n4. If any phase shows 0Ω or near-zero, there's a short\n5. Check motor connector pins for bent/touching contacts\n6. Check for damaged insulation on motor cables",
        source: "AMC_AppNote_009.pdf p.1"
      },
      q_analog_step4: {
        question: "Motor wiring is good. Now **connect the command signal** (±10V analog or step/dir). Apply a small command. Does the motor respond correctly?",
        options: [
          { label: "Yes, motor moves correctly", next: "a_analog_noise_issue" },
          { label: "Motor doesn't move", next: "a_analog_command_issue" },
          { label: "LED turns red when command is applied", next: "a_analog_command_fault" },
        ]
      },
      a_analog_noise_issue: {
        answer: "The drive works with isolated connections but faults in the full system. This is likely an **electrical noise issue**.\n\n**Fix:**\n1. Use shielded cables for all signal connections\n2. Ground shields at the drive end only\n3. Route signal cables away from power cables\n4. Add ferrite cores on signal cables (see App Note 023)\n5. Ensure a single-point ground scheme\n6. Check for ground loops between the controller and drive",
        source: "AMC_AppNote_009.pdf p.1, AMC_AppNote_023.pdf"
      },
      a_analog_command_issue: {
        answer: "Motor doesn't respond to command:\n\n1. **Check command input** — measure voltage at the drive's +REF IN / -REF IN pins. Should see the command voltage.\n2. **Check GAIN potentiometer** — may be set too low (no output)\n3. **Check operating mode** — DIP switches must match your command type (voltage mode, current mode)\n4. **Check current limit** — if set to zero, no output\n5. **For step/dir** — verify pulse signal is present and at correct logic level",
        source: "AMC_HWManual_AnalogDrives.pdf p.54"
      },
      a_analog_command_fault: {
        answer: "Fault when command is applied = **tuning or motor mismatch issue**.\n\n1. **Over-current** — command signal may be driving too much current. Reduce command level.\n2. **Incorrect motor type** — DIP switches must match (brushed vs brushless)\n3. **Wrong current mode** — verify current limit DIP switches and potentiometer\n4. **Oscillation** — if using velocity mode, reduce gain\n5. Start with a small command signal (0.5V) and increase gradually",
        source: "AMC_AppNote_009.pdf, AMC_AppNote_011.pdf"
      },

      // --- Generic / unsure branch ---
      q_generic_red: {
        question: "A red LED generally means the drive's power bridge is disabled. Can you connect to the drive with software (ACE or DriveWare)?",
        options: [
          { label: "Yes, I can connect", next: "a_generic_read_software" },
          { label: "No, can't connect to the drive", next: "a_generic_no_software" },
        ]
      },
      a_generic_read_software: {
        answer: "**Read the fault code in your software:**\n\n- **ACE** (FlexPro): Dashboard tab shows fault name and Safety Actions\n- **DriveWare** (DigiFlex): Bridge Status panel shows fault, Status Register has details\n\nCommon faults across all drives:\n- Over-voltage (DC bus too high)\n- Over-current (motor short or tuning issue)\n- Over-temperature (insufficient cooling)\n- Hall State Error (feedback wiring)\n- STO Active (safety input not energized)\n- Communication Error (network timeout)\n\nClear fault with Fault Reset button or cycle power.",
        source: "AMC_SW_Manual_ACE.pdf p.138-139"
      },
      a_generic_no_software: {
        answer: "Without software, check these common causes:\n\n1. **Power supply** — verify DC bus voltage is within the drive's rated range\n2. **Enable/Inhibit pins** — all enable inputs must be active\n3. **STO inputs** — if present, both need +24V\n4. **Motor wiring** — disconnect motor, see if LED turns green\n5. **Feedback** — disconnect encoder/Halls, see if LED turns green\n6. **DIP switches** — verify they match your motor and operating mode\n\nIf LED stays red with ONLY power connected, the drive may be damaged.",
        source: "AMC_AppNote_009.pdf p.1"
      },

      // Shared answers
      a_no_power: {
        answer: "**No LEDs lit = no power to the drive.**\n\nCheck:\n1. DC power supply is ON and outputting correct voltage\n2. Fuse is intact (check inline fuse or circuit breaker)\n3. Power connector is fully seated\n4. Wiring polarity is correct (HV+ and HV-)\n5. If using AC input, verify AC mains and check the drive's internal rectifier fuse\n6. Measure voltage at the drive's power input terminals with a multimeter",
        source: "AMC_HWManual_DigiFlex_Panel_EtherCAT.pdf"
      },
    }
  },

  // =========================================================================
  // TREE 2: MOTOR WON'T SPIN
  // =========================================================================
  motor_wont_spin: {
    title: "Motor Won't Spin",
    trigger_keywords: [
      "motor won't spin", "motor not spinning", "motor doesn't move",
      "motor won't move", "no motion", "motor not moving", "won't rotate",
      "motor not responding", "motor won't run", "no output", "drive won't run"
    ],
    root: "q_led_color",
    nodes: {
      q_led_color: {
        question: "What color is the drive's Status LED?",
        options: [
          { label: "Green (solid or blinking)", next: "q_command_present" },
          { label: "Red", next: "a_motor_led_red" },
          { label: "Off / no LEDs", next: "a_motor_no_power" },
        ]
      },
      a_motor_led_red: {
        answer: "The drive has a **fault** (red LED = bridge disabled). The motor can't run until the fault is cleared.\n\nUse the **Red LED troubleshooter** above or connect with ACE/DriveWare to read the specific fault code. Common causes: over-voltage, over-current, invalid Hall state, STO active.",
        source: "See Red LED troubleshooting tree"
      },
      a_motor_no_power: {
        answer: "**No power to the drive.** Check:\n1. DC supply voltage and fuse\n2. Power connector is seated\n3. Wiring polarity (HV+/HV-)\n4. AC mains if using AC input",
        source: "AMC_HWManual_DigiFlex_Panel_EtherCAT.pdf"
      },
      q_command_present: {
        question: "LED is green, so the drive is enabled. Is a command signal being sent to the drive?",
        options: [
          { label: "Yes, I'm sending a command", next: "q_command_type" },
          { label: "Not sure / How do I check?", next: "a_check_command" },
          { label: "No, I haven't sent a command yet", next: "a_send_command" },
        ]
      },
      a_check_command: {
        answer: "**How to verify command signal:**\n\n- **Analog (±10V):** Measure voltage at the drive's command input pins with a multimeter. Should be non-zero.\n- **Network (EtherCAT/CANopen):** Check your master is sending non-zero target values. In ACE/DriveWare, use the oscilloscope to monitor the command reference.\n- **Step/Dir:** Verify pulse train is present with an oscilloscope or logic analyzer.\n\nIf command is present but motor doesn't move, the issue is likely operating mode, feedback, or current limits.",
        source: "AMC_HWManual_DigiFlex_Panel_EtherCAT.pdf"
      },
      a_send_command: {
        answer: "The drive is enabled but no command has been sent — that's why the motor isn't moving.\n\n**To test:** In ACE or DriveWare, use the **Jog** function to send a small velocity command. If the motor moves, the drive is working and the issue is in your command source (PLC, controller, analog signal).",
        source: "AMC_SW_Manual_ACE.pdf"
      },
      q_command_type: {
        question: "What operating mode is the drive in?",
        options: [
          { label: "Position mode", next: "a_position_mode_check" },
          { label: "Velocity mode", next: "a_velocity_mode_check" },
          { label: "Current/Torque mode", next: "a_current_mode_check" },
          { label: "Not sure", next: "a_check_mode" },
        ]
      },
      a_position_mode_check: {
        answer: "In **Position mode**, the motor won't move unless the target position differs from actual position.\n\n**Check:**\n1. Target position is different from current position\n2. Encoder/feedback is connected and reading correctly — if position reads 0 constantly, the encoder is disconnected\n3. Profile velocity and acceleration are non-zero\n4. Position limits (software) aren't preventing motion\n5. In-position window — drive may think it's already at target",
        source: "AMC_SW_Manual_ACE.pdf"
      },
      a_velocity_mode_check: {
        answer: "In **Velocity mode**, the motor should spin at the commanded speed.\n\n**Check:**\n1. Target velocity is non-zero\n2. Velocity loop is tuned — if gains are zero, no output\n3. Current limits aren't set to zero\n4. Feedback (encoder) is connected and counting\n5. The command polarity is correct (negative command may try to spin the wrong way into a hard stop)",
        source: "AMC_SW_Manual_ACE.pdf"
      },
      a_current_mode_check: {
        answer: "In **Current/Torque mode**, the drive outputs current proportional to the command.\n\n**Check:**\n1. Current command is non-zero\n2. Peak and continuous current limits are set above zero\n3. Motor is not mechanically locked/stalled (current is flowing but can't overcome friction)\n4. Commutation is correct — if electrical angle is wrong, current may fight the motor\n5. Run auto-commutation if using brushless motor",
        source: "AMC_SW_Manual_ACE.pdf, AMC_AppNote_015.pdf"
      },
      a_check_mode: {
        answer: "**To check operating mode:**\n\n- **ACE:** Parameter Tree → Operation Mode (object 6060h)\n- **DriveWare:** Operating Mode dropdown on main screen\n- **Modes:** Profile Position (1), Profile Velocity (3), Torque/Current (4), Cyclic Sync Position (8), Cyclic Sync Velocity (9), Cyclic Sync Torque (10)\n\nMake sure the mode matches your application. If using network control, the master must set this via the Modes of Operation object.",
        source: "AMC_CommManual_FP_EtherCAT.pdf"
      },
    }
  },

  // =========================================================================
  // TREE 3: COMMUNICATION ERROR
  // =========================================================================
  comm_error: {
    title: "Communication Error",
    trigger_keywords: [
      "can't connect", "cannot connect", "communication error", "no communication",
      "won't communicate", "connection failed", "can't communicate", "timeout",
      "not responding", "connection lost", "network error", "bus error"
    ],
    root: "q_protocol",
    nodes: {
      q_protocol: {
        question: "What communication method are you trying to use?",
        options: [
          { label: "USB (ACE or DriveWare)", next: "q_usb_issue" },
          { label: "EtherCAT", next: "q_ecat_issue" },
          { label: "CANopen", next: "q_canopen_issue" },
          { label: "RS-485 / Serial / Modbus", next: "q_serial_issue" },
        ]
      },

      // USB
      q_usb_issue: {
        question: "Does the drive appear in Windows Device Manager when plugged in?",
        options: [
          { label: "Yes, I see it", next: "a_usb_software" },
          { label: "No, nothing appears", next: "a_usb_driver" },
          { label: "It shows with a yellow warning icon", next: "a_usb_driver_error" },
        ]
      },
      a_usb_software: {
        answer: "The USB driver is working. The issue is in the software settings:\n\n1. **Correct COM port** — in ACE/DriveWare, select the right COM port\n2. **Drive address** — default is 63 for FlexPro. Try scanning (Communication → Scan)\n3. **Baud rate** — default is 9600 for DigiFlex RS-232\n4. **Close other software** — only one application can use the COM port at a time\n5. **Try ACE instead of DriveWare** (or vice versa) — FlexPro uses ACE, DigiFlex uses DriveWare",
        source: "AMC_AppNote_050.pdf p.1"
      },
      a_usb_driver: {
        answer: "**USB device not detected:**\n\n1. Try a **different USB cable** — data cables look identical to charge-only cables\n2. Try a **different USB port** (avoid hubs, use direct motherboard port)\n3. **Drive must be powered** — USB alone doesn't power the drive\n4. **Install the driver** — download from a-m-c.com or let Windows search automatically\n5. On Windows 10/11, the driver should install automatically. If not, try Windows Update.\n6. Try **unplug → wait 10 seconds → replug**",
        source: "AMC_AppNote_050.pdf p.1"
      },
      a_usb_driver_error: {
        answer: "**Yellow warning = driver error:**\n\n1. Right-click the device → **Uninstall Device** (check 'delete driver software')\n2. Unplug the USB cable\n3. Wait 10 seconds\n4. Replug — Windows should reinstall the driver\n5. If it still fails, try a different USB port\n6. Download the latest driver from a-m-c.com if Windows can't find one",
        source: "AMC_AppNote_050.pdf p.1"
      },

      // EtherCAT
      q_ecat_issue: {
        question: "What EtherCAT state is the drive stuck in? Check your master's device list.",
        options: [
          { label: "Not detected at all", next: "a_ecat_not_found" },
          { label: "Stuck in INIT or PRE-OP", next: "a_ecat_stuck_init" },
          { label: "Stuck in SAFE-OP (won't go to OP)", next: "a_ecat_stuck_safeop" },
          { label: "Was working, now lost connection", next: "a_ecat_lost" },
        ]
      },
      a_ecat_not_found: {
        answer: "**EtherCAT drive not detected by master:**\n\n1. **Check cables** — use Cat5e or better. EtherCAT is daisy-chained: Master OUT → Drive IN, Drive OUT → next Drive IN\n2. **Check IN/OUT ports** — they're NOT interchangeable. IN receives from upstream, OUT sends downstream\n3. **Drive power** — the drive must be powered for EtherCAT to work\n4. **Check link LEDs** — the RJ-45 connectors should have green link lights\n5. **ESI/XML file** — your master needs the correct XML device description. Download from a-m-c.com > Downloads > Device Description Files",
        source: "AMC_CommManual_FP_EtherCAT.pdf"
      },
      a_ecat_stuck_init: {
        answer: "**Drive stuck in Init or Pre-Operational:**\n\n1. **Wrong ESI file** — the XML must match the drive's firmware version. Re-download from a-m-c.com\n2. **Mailbox configuration** — check sync manager SM0/SM1 configuration matches the ESI\n3. **Check master error log** — it will show why the transition failed\n4. **Firmware mismatch** — if the drive was recently updated, re-scan in the master\n5. Try removing and re-adding the drive in the master's configuration",
        source: "AMC_CommManual_FP_EtherCAT.pdf p.24"
      },
      a_ecat_stuck_safeop: {
        answer: "**Drive stuck in Safe-Operational (won't transition to OP):**\n\n1. **PDO mapping error** — the master's PDO configuration must match the drive's supported objects. Check TxPDO/RxPDO assignments\n2. **Distributed Clocks** — if using Cyclic Synchronous modes (CSP/CSV/CST), DC sync must be enabled and configured correctly\n3. **Watchdog timeout** — the master must send process data within the watchdog period\n4. **Check master error log** — specific error code will indicate the cause\n5. Try switching to Profile modes first (they don't require DC sync)",
        source: "AMC_CommManual_FP_EtherCAT.pdf p.24, AMC_AppNote_017.pdf"
      },
      a_ecat_lost: {
        answer: "**EtherCAT connection lost during operation:**\n\n1. **Cable issue** — check all Ethernet cables for damage, loose connectors, or strain\n2. **EMI/noise** — route Ethernet cables away from motor power cables. Use shielded cables\n3. **Grounding** — verify single-point ground scheme between master and drives\n4. **Frame drops** — check master diagnostics for CRC errors or lost frames\n5. **Power interruption** — if the drive briefly lost power, it will drop off the bus\n6. **Topology** — if one drive in a chain loses power, all downstream drives lose connection",
        source: "AMC_CommManual_FP_EtherCAT.pdf"
      },

      // CANopen
      q_canopen_issue: {
        question: "Can you see ANY communication on the CAN bus? (Check with a CAN analyzer or master diagnostics)",
        options: [
          { label: "No, bus is completely silent", next: "a_canopen_silent" },
          { label: "I see some messages but drive doesn't respond", next: "a_canopen_no_response" },
          { label: "Getting error frames / bus errors", next: "a_canopen_bus_error" },
        ]
      },
      a_canopen_silent: {
        answer: "**Completely silent CAN bus:**\n\n1. **Wiring** — verify CAN_H, CAN_L, and GND are connected correctly\n2. **Termination** — you need **120Ω at EACH END** of the bus. Measure with power off: should read ~60Ω between CAN_H and CAN_L\n3. **Baud rate** — all devices must use the same baud rate. Default is 250 kbit/s for DigiFlex\n4. **Transceiver** — CAN controller needs a working transceiver chip\n5. **Swapped H/L** — try swapping CAN_H and CAN_L wires\n6. **Bus length** — max depends on baud rate: 1M=25m, 500k=100m, 250k=250m",
        source: "AMC_CommManual_CANopen.pdf, AMC_AppNote_005.pdf"
      },
      a_canopen_no_response: {
        answer: "**Drive not responding to commands:**\n\n1. **Node ID** — each device needs a unique ID (1-127). Check the drive's configured ID in DriveWare\n2. **NMT state** — send NMT Start Remote Node (0x01) to the drive's node ID to transition it to Operational\n3. **COB-ID mismatch** — SDO and PDO COB-IDs must match between master and drive\n4. **Wrong baud rate** — if the baud rates differ, you'll see data but it won't decode correctly\n5. **Heartbeat/Guard** — check if the drive is expecting heartbeat monitoring",
        source: "AMC_CommManual_CANopen.pdf p.10"
      },
      a_canopen_bus_error: {
        answer: "**CAN bus errors / error frames:**\n\n1. **Missing termination** — 120Ω at each end of the bus (most common cause)\n2. **Stub lengths too long** — keep drop lines under 0.3m at 1 Mbit/s\n3. **Ground issues** — all devices must share a common GND reference\n4. **Cable quality** — use twisted pair, shielded cable\n5. **Baud rate mismatch** — even one device at the wrong baud causes bus errors for everyone\n6. **Damaged transceiver** — a faulty CAN transceiver on any device can corrupt the bus",
        source: "AMC_AppNote_005.pdf, AMC_CommManual_CANopen.pdf"
      },

      // Serial
      q_serial_issue: {
        question: "What interface are you using?",
        options: [
          { label: "RS-232 (direct serial)", next: "a_rs232" },
          { label: "RS-485 (multi-drop bus)", next: "a_rs485" },
          { label: "Modbus RTU", next: "a_modbus" },
        ]
      },
      a_rs232: {
        answer: "**RS-232 troubleshooting:**\n\n1. **TX/RX swap** — try swapping the TX and RX wires (null modem)\n2. **Baud rate** — default is 9600 for DigiFlex. Must match on both ends\n3. **Cable length** — RS-232 max is ~15m (50 feet)\n4. **COM port** — verify you're using the correct COM port in software\n5. **Ground** — signal ground must be connected between both devices\n6. **USB-to-Serial adapter** — some adapters don't work reliably. Try a different one.",
        source: "AMC_CommManual_RS485.pdf, AMC_AppNote_050.pdf"
      },
      a_rs485: {
        answer: "**RS-485 troubleshooting:**\n\n1. **Termination** — 120Ω at each end of the bus\n2. **Polarity** — Data+ (A) to Data+ (A), Data- (B) to Data- (B). Some manufacturers swap A/B\n3. **Address** — each drive needs a unique address. Default varies by model\n4. **Baud rate** — must match all devices on the bus\n5. **Half-duplex timing** — RS-485 is half-duplex; the master must release the bus after transmitting\n6. **Cable** — use twisted pair, shielded\n7. **Max bus length** — 1200m (4000 ft) at 9600 baud",
        source: "AMC_CommManual_RS485.pdf, AMC_AppNote_006.pdf"
      },
      a_modbus: {
        answer: "**Modbus RTU troubleshooting:**\n\n1. **Slave address** — default varies by model. Check in DriveWare\n2. **Baud rate** — must match master. Common: 9600, 19200, 38400, 115200\n3. **Parity** — verify even/odd/none matches between master and drive\n4. **Register addresses** — Modbus uses register numbers. See the Modbus Communication Manual for the register map\n5. **Function codes** — supported: 03 (Read Holding), 06 (Write Single), 16 (Write Multiple)\n6. **Wiring** — same as RS-485 (Modbus RTU runs on RS-485 physical layer)\n7. **Timing** — Modbus requires 3.5 character silence between frames",
        source: "AMC_CommManual_Modbus.pdf"
      },
    }
  },

  // =========================================================================
  // TREE 4: JERKY / ROUGH MOTION
  // =========================================================================
  jerky_motion: {
    title: "Jerky or Rough Motion",
    trigger_keywords: [
      "jerky motion", "rough motion", "motor vibrat", "oscillat",
      "motor shaking", "motor jitter", "cogging", "rough running",
      "unstable", "motor hunting", "noisy motor", "motor noise",
      "jerky", "vibration", "rough"
    ],
    root: "q_when_jerky",
    nodes: {
      q_when_jerky: {
        question: "When does the jerky/rough motion occur?",
        options: [
          { label: "At standstill (motor vibrates without moving)", next: "q_standstill_type" },
          { label: "During motion (jerky while spinning)", next: "q_motion_speed" },
          { label: "Only during acceleration/deceleration", next: "a_accel_jerk" },
          { label: "Only at specific positions", next: "a_encoder_issue" },
        ]
      },
      q_standstill_type: {
        question: "How would you describe the vibration at standstill?",
        options: [
          { label: "Constant buzzing/humming", next: "a_current_loop" },
          { label: "Motor snaps to a position then oscillates", next: "a_commutation" },
          { label: "Random jerks or twitches", next: "a_encoder_noise" },
        ]
      },
      a_current_loop: {
        answer: "**Constant buzzing at standstill = current loop tuning issue.**\n\nThe current loop gains (Kp/Ki) are too high, causing oscillation.\n\n**Fix:**\n1. In ACE: Tuning tab → Current Loop\n2. **Reduce Kp (proportional gain)** first — cut it in half\n3. If still buzzing, reduce Ki (integral gain)\n4. Or use **Auto-Tune** in ACE — it will set optimal gains for your motor\n5. Target current loop bandwidth: 1-3 kHz\n6. Verify motor inductance (Ls) and resistance (Rs) are set correctly in Motor Parameters",
        source: "AMC_AppNote_015.pdf p.1"
      },
      a_commutation: {
        answer: "**Motor snapping to position then oscillating = incorrect commutation (electrical angle offset).**\n\nThe drive doesn't know the correct electrical angle of the rotor.\n\n**Fix:**\n1. Run **Auto-Commutation** in ACE (Tuning → Commutation → Auto-Commute)\n2. Prerequisites: current loop must be tuned first, motor must be free to rotate\n3. If auto-commutation fails, check: Hall sensor wiring, motor pole count, commutation type (sinusoidal vs trapezoidal)\n4. For motors with absolute encoders, commutation is automatic after first phase detection",
        source: "AMC_AppNote_014.pdf, AMC_SW_Manual_ACE.pdf"
      },
      a_encoder_noise: {
        answer: "**Random jerks/twitches = encoder signal noise.**\n\nNoisy encoder signals cause position jumps that the servo loop tries to correct, creating jerky motion.\n\n**Fix:**\n1. Use **differential encoder signals** (A+/A-, B+/B-) — not single-ended\n2. Use **shielded cable** — ground the shield at the drive end only\n3. Route encoder cables **away from motor power cables** (at least 6 inches / 15cm)\n4. Add ferrite cores on the encoder cable near the drive\n5. Check encoder cable length — long runs are more susceptible to noise\n6. In ACE, use the oscilloscope to monitor position feedback — look for jumps",
        source: "AMC_AppNote_040.pdf, AMC_HWManual_FlexPro_PCB.pdf"
      },
      q_motion_speed: {
        question: "At what speed does the roughness occur?",
        options: [
          { label: "Low speed (slow motion is rough)", next: "a_low_speed_cogging" },
          { label: "High speed (smooth at low speed, rough when fast)", next: "a_high_speed" },
          { label: "All speeds", next: "a_velocity_loop" },
        ]
      },
      a_low_speed_cogging: {
        answer: "**Rough motion at low speed** is usually caused by:\n\n1. **Motor cogging** — permanent magnet motors have inherent cogging torque at low speeds. This is normal but can be reduced by:\n   - Increasing encoder resolution (more counts per rev)\n   - Using sinusoidal commutation instead of trapezoidal\n   - Reducing current loop bandwidth slightly\n2. **Low encoder resolution** — fewer counts per rev means the servo loop has coarser position feedback at low speeds\n3. **Velocity loop gains too high** — reduce velocity Kp at low speeds\n4. **Friction** — check mechanical system for stiction (static friction higher than dynamic friction)",
        source: "AMC_AppNote_015.pdf"
      },
      a_high_speed: {
        answer: "**Rough at high speed** is usually caused by:\n\n1. **Velocity loop too aggressive** — reduce velocity Kp and/or increase velocity Ki\n2. **Current loop bandwidth too low** — if the current loop can't keep up at high speed, motion gets rough. Try auto-tune.\n3. **Encoder noise at high speed** — signal integrity degrades at high count rates. Check cabling.\n4. **Mechanical resonance** — the coupling or load may have a resonance at that speed. Try a different speed range.\n5. **Bus voltage too low** — at high speeds, back-EMF approaches bus voltage and the drive can't deliver enough current. Check that bus voltage > motor Ke × speed.",
        source: "AMC_AppNote_015.pdf, AMC_AppNote_041.pdf"
      },
      a_velocity_loop: {
        answer: "**Rough at all speeds = velocity loop tuning issue.**\n\n**Fix:**\n1. **Reduce velocity Kp** (proportional gain) — cut by 50%\n2. Gradually increase until motion is smooth but responsive\n3. Target velocity loop bandwidth: **50-200 Hz** (much lower than current loop)\n4. **Increase velocity Ki** (integral) for steady-state accuracy, but too high causes oscillation\n5. Use ACE's **Auto-Tune** which tunes both current and velocity loops\n6. Check that current loop is tuned FIRST — the velocity loop sits on top of it",
        source: "AMC_AppNote_015.pdf p.1"
      },
      a_accel_jerk: {
        answer: "**Rough only during acceleration/deceleration:**\n\n1. **Acceleration rate too high** — the motor can't follow the commanded ramp. Reduce acceleration value.\n2. **S-curve profiling** — enable jerk limiting (S-curve) instead of trapezoidal profiles. This smooths the transitions.\n3. **Current limit hit** — during acceleration, current demand spikes. If it hits the limit, motion jerks. Increase peak current limit or reduce accel rate.\n4. **Mechanical coupling** — flexible couplings or belts can cause resonance during acceleration. Check for backlash.",
        source: "AMC_SW_Manual_ACE.pdf"
      },
      a_encoder_issue: {
        answer: "**Rough at specific positions = encoder or mechanical issue.**\n\n1. **Encoder defect** — if the encoder has a damaged track, it will misread at specific positions. Test by moving the motor slowly through the problem area while watching position in ACE scope.\n2. **Motor cogging** — some motors have stronger cogging at certain positions (near pole transitions)\n3. **Mechanical interference** — check for something physically contacting the motor or load at those positions\n4. **Hall sensor issue** — if using Hall+encoder, the Hall transitions at 6 positions/rev can cause bumps if commutation offset is wrong. Run auto-commutation.",
        source: "AMC_AppNote_040.pdf, AMC_AppNote_014.pdf"
      },
    }
  },
};
