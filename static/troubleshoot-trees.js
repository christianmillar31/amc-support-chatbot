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
          { label: "All speeds — physical vibration or buzz", next: "a_velocity_loop" },
          { label: "All speeds — scope shows bad tracking but motion feels smooth", next: "q_scope_tracking_shape" },
        ]
      },
      q_scope_tracking_shape: {
        question: "On the scope (velocity command vs velocity feedback), what does the error look like?",
        options: [
          { label: "Feedback lags command — slow rise time, can't keep up", next: "a_tracking_underpowered" },
          { label: "Constant offset — feedback runs parallel to command but never reaches it", next: "a_tracking_steady_state" },
          { label: "Overshoot and ringing at each step", next: "a_velocity_loop" },
          { label: "Feedback line is noisy/jagged (even though motor feels OK)", next: "a_encoder_noise" },
        ]
      },
      a_tracking_underpowered: {
        answer: "**Smooth but sluggish = velocity loop gains too LOW, or the drive is saturating.**\n\nThis is the OPPOSITE of the jerky case — your feedback can't keep up.\n\n**Fix (in order):**\n1. **Increase velocity Kp** (proportional gain) in small steps until rise time is acceptable. Opposite direction from the jerky-case advice.\n2. **Increase velocity Ki** for steady-state accuracy.\n3. **Check for saturation** — in ACE's scope, monitor commanded current. If it's pegged at the current limit during acceleration, the drive is delivering everything it has and physics won't let it track faster. Either raise the current limit (if safe for the motor) or reduce acceleration.\n4. **Check bus voltage** — at the saturating speed, back-EMF may be eating all the headroom. Bus voltage must be > motor Ke × speed + IR drop.\n5. **Verify the current loop is tuned FIRST** — an under-tuned current loop makes velocity loop gains look ineffective.",
        source: "AMC_AppNote_015.pdf, AMC_SW_Manual_ACE.pdf"
      },
      a_tracking_steady_state: {
        answer: "**Constant offset from command = missing or insufficient integral action.**\n\nProportional gain alone can't drive steady-state error to zero. You need Ki.\n\n**Fix:**\n1. **Increase velocity Ki** (integral gain) — start by doubling the current value\n2. Watch for oscillation as Ki climbs — if it starts ringing, back off 20%\n3. If Ki is already high and error persists, check for **feed-forward** (some drives have a velocity or acceleration feed-forward term that pre-compensates the command)\n4. For position loops: a steady-state following error during constant velocity is normal — it scales with velocity / Kp. This is called **following error** and is usually expected; only worry if the error exceeds your position-error-limit fault threshold.\n5. Check that the motor isn't **current-limited** in the direction of the error — a drive capped at low current will look like steady-state offset.",
        source: "AMC_AppNote_015.pdf, AMC_SW_Manual_ACE.pdf"
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
        answer: "**Rough at all speeds (physical vibration/buzz) = velocity loop gains too HIGH.**\n\nThe loop is oscillating around the setpoint — that's the jerk you feel.\n\n**Fix (in order):**\n1. **Reduce velocity Kp** (proportional gain) — cut by 50%\n2. Gradually increase until motion is smooth but responsive\n3. Target velocity loop bandwidth: **50-200 Hz** (much lower than current loop)\n4. **Reduce velocity Ki** (integral) if a constant buzz persists at standstill — that's an integrator limit-cycle\n5. If smooth motion requires very low gains, your **current loop may need tuning first** — an under-tuned current loop forces the velocity loop to compensate and adds jitter\n6. Use ACE's **Auto-Tune** which tunes both current and velocity loops in sequence\n\n**⚠ Don't confuse this with the opposite case:** if your scope shows the feedback smoothly LAGGING the command (no ringing), that's gains too LOW, not high. Go back one step and pick \"scope shows bad tracking but motion feels smooth\" instead.",
        source: "AMC_AppNote_015.pdf p.1, AMC_AppNote_037.pdf"
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

  // =========================================================================
  // TREE 5: SCOPE TUNING GUIDE (current / velocity / position loops)
  // Sources: AppNote 015 (current loop tuning), AppNote 037 (current tuning
  // for DigiFlex), ACE Manual (scope/tuning), AppNote 011 (analog setup).
  // =========================================================================
  scope_tuning: {
    title: "Tuning a Loop on the Oscilloscope",
    trigger_keywords: [
      "tune loop", "tuning", "loop tuning", "current loop", "velocity loop",
      "position loop", "auto tune", "auto-tune", "autotune",
      "loop gains", "kp", "ki", "kd", "bandwidth", "step response",
      "scope tuning", "scope shows", "bad tracking", "tracking error",
      "following error", "overshoot", "undershoot",
    ],
    root: "q_tune_which_loop",
    nodes: {
      q_tune_which_loop: {
        question: "Which loop are you tuning? (Loops nest outside-in — position wraps velocity, velocity wraps current. Tune the innermost loop first.)",
        options: [
          { label: "Current loop (torque loop) — innermost", next: "q_tune_current_step" },
          { label: "Velocity loop — wraps current loop", next: "q_tune_velocity_step" },
          { label: "Position loop — outermost", next: "q_tune_position_step" },
          { label: "Not sure / I just want auto-tune", next: "a_tune_autotune" },
        ]
      },

      // --- Current loop ---
      q_tune_current_step: {
        question: "Current-loop step response shows:",
        options: [
          { label: "Ringing / overshoot then oscillation", next: "a_current_too_hot" },
          { label: "Slow rise time, never catches up", next: "a_current_too_cold" },
          { label: "Clean step but small steady-state error", next: "a_current_needs_ki" },
          { label: "Ran auto-tune and it failed", next: "a_current_autotune_fail" },
          { label: "Not sure what I'm looking at", next: "a_current_how_to_scope" },
        ]
      },
      a_current_how_to_scope: {
        answer: "**How to scope the current loop** (ACE / DriveWare):\n\n1. Open the **Scope/Tuning** window.\n2. Set the **Waveform Generator** to inject a **100 Hz square wave** into the **current command** input. Start with a small amplitude (~10–20% of the drive's continuous current rating).\n3. It may be necessary to **clamp the rotor** — an unloaded motor will move during tuning.\n4. Plot **Current Command** and **Current Feedback** on the scope.\n5. A well-tuned current loop has: **fast rise time, <20% overshoot, <2 ms settling, no sustained ringing**. Typical bandwidth target: **1–3 kHz**.\n6. Adjust **Kp first** (fastest response without ringing), then increase **Ki** to drive steady-state error to zero without causing oscillation.",
        source: "AMC_AppNote_015.pdf p.1-2, AMC_AppNote_037.pdf p.1"
      },
      a_current_too_hot: {
        answer: "**Ringing on current step = current-loop Kp is too high.**\n\n**Fix:**\n1. **Reduce Kp** in 25% steps until the ringing stops and the step looks critically damped (fast rise, minimal overshoot).\n2. If Kp is already small and ringing continues, reduce **Ki** as well — an aggressive integrator also causes oscillation.\n3. Verify **motor inductance (Ls) and resistance (Rs)** are set correctly in the motor parameters; wrong values push Kp into the unstable region.\n4. Check that the **PWM / switching frequency** is appropriate for the motor's electrical time constant (L/R). Low-inductance motors need higher switching frequencies.",
        source: "AMC_AppNote_015.pdf p.2, AMC_AppNote_037.pdf"
      },
      a_current_too_cold: {
        answer: "**Slow rise time = current-loop Kp is too low** (under-damped toward critically-damped).\n\n**Fix:**\n1. **Increase Kp** in 25% steps until the feedback catches the command cleanly. Stop just before ringing starts — then back off 10–20%.\n2. Check that you're actually **applying enough current command** — a tiny step signal will produce a tiny response. Scale the square-wave amplitude up.\n3. Verify motor parameters (Ls, Rs). Wrong values make the loop look sluggish.\n4. For AMC analog drives: check DIP switch configuration — the current-limit setting caps the achievable rate of change.",
        source: "AMC_AppNote_015.pdf p.2, AMC_AppNote_037.pdf"
      },
      a_current_needs_ki: {
        answer: "**Clean step + steady-state error = need more Ki.**\n\n**Fix:**\n1. **Increase Ki** in 25% steps. The integrator drives residual error to zero.\n2. Watch for oscillation as Ki climbs; stop at the first sign and back off 20%.\n3. The current loop is usually dominated by Kp — Ki tends to be small. If Ki already looks big, the issue may be a **DC offset in the current sensor** or a **misconfigured motor resistance (Rs)** rather than a tuning problem.",
        source: "AMC_AppNote_015.pdf p.2"
      },
      a_current_autotune_fail: {
        answer: "**Auto-tune failure** on the current loop usually means the drive can't meet its sanity checks. Common causes:\n\n1. **Motor won't turn** — is it clamped to ground or jammed? Auto-tune must be able to inject a small current and observe the response.\n2. **Missing motor parameters** — motor Ls (inductance), Rs (resistance), and pole count must be entered before auto-tune.\n3. **Feedback not configured** — auto-tune reads current sensors but also needs the encoder or Hall sensors to identify motor constants.\n4. **Fault present** — if the drive is already in an overtemp / overvoltage / Hall-fault state, auto-tune aborts.\n5. **Wrong commutation** — if motor phase order is wrong, auto-tune sees non-sensical responses. Try manual commutation first (see AN-014).\n\nAfter fixing the blocker, re-run auto-tune. If it still fails, fall back to manual tuning per AN-015.",
        source: "AMC_AppNote_014.pdf, AMC_AppNote_015.pdf, AMC_SW_Manual_ACE.pdf"
      },

      // --- Velocity loop ---
      q_tune_velocity_step: {
        question: "Velocity-loop step response shows:",
        options: [
          { label: "Overshoot + ringing on each step", next: "a_vel_too_hot" },
          { label: "Sluggish / lags command (smooth but slow)", next: "a_tracking_underpowered" },
          { label: "Reaches command but with steady offset", next: "a_tracking_steady_state" },
          { label: "Buzzing/oscillating at standstill", next: "a_vel_standstill_hunt" },
          { label: "Not sure what I'm looking at", next: "a_vel_how_to_scope" },
        ]
      },
      a_vel_how_to_scope: {
        answer: "**How to scope the velocity loop** (prerequisite: current loop already tuned).\n\n1. **Scope/Tuning** window → **Waveform Generator** → inject a **velocity step command** (e.g. 10% of rated speed) at a frequency low enough to see the whole transient (~5–10 Hz square wave).\n2. Plot **Velocity Command** and **Velocity Feedback**.\n3. A well-tuned velocity loop: **fast rise (2–10 ms), minimal overshoot, <1% steady-state error, no sustained oscillation**. Target bandwidth: **50–200 Hz** (much lower than the current loop).\n4. Tune **Kp first**, then add **Ki** for zero steady-state error. If the drive exposes a feed-forward term, add it last to improve response without reducing stability margin.",
        source: "AMC_AppNote_015.pdf p.1, AMC_SW_Manual_ACE.pdf"
      },
      a_vel_too_hot: {
        answer: "**Overshoot + ringing = velocity Kp is too high.**\n\nSame fix as the jerky-motion tree's velocity case: reduce Kp 50%, step up until just-smooth, then back off 20%. See also: verify the current loop is solid first (velocity loop sits on top of it — a shaky current loop makes the velocity loop look untunable).",
        source: "AMC_AppNote_015.pdf p.1"
      },
      a_vel_standstill_hunt: {
        answer: "**Buzzing at zero velocity = integrator limit cycle.**\n\nAt rest the commanded velocity is zero, but any friction or cogging keeps the feedback slightly off. The integrator keeps accumulating error until it overshoots, then unwinds, causing a slow hunt.\n\n**Fix:**\n1. **Reduce velocity Ki** — the integrator is too aggressive for your friction level.\n2. Add a small **dead-band** around zero if your drive supports it.\n3. Check for **mechanical stiction** — if the system is sticky, the loop fights it. Greasing or realigning can help.\n4. Verify the velocity feedback isn't quantized (low encoder resolution + high Kp causes discrete hunting).",
        source: "AMC_AppNote_015.pdf"
      },

      // --- Position loop ---
      q_tune_position_step: {
        question: "Position-loop response shows:",
        options: [
          { label: "Overshoot past target, then corrects back", next: "a_pos_too_hot" },
          { label: "Reaches target slowly, large following error", next: "a_pos_too_cold" },
          { label: "Oscillates around target and can't settle", next: "a_pos_oscillates" },
          { label: "Position-error fault trips during moves", next: "a_pos_error_fault" },
        ]
      },
      a_pos_too_hot: {
        answer: "**Position overshoot = position Kp too high, or derivative (Kd) too low to damp it.**\n\n**Fix:**\n1. **Reduce position Kp** first.\n2. **Add or increase Kd** (derivative gain) to damp overshoot without lowering bandwidth. Typical ratio: Kd ≈ Kp / (20–40).\n3. Enable **S-curve profiling** on position moves — a trapezoidal profile has infinite jerk at the corners which loads overshoot into the position loop.\n4. Verify **velocity loop is solid** — a resonant velocity loop causes visible overshoot in the position loop above it.",
        source: "AMC_SW_Manual_ACE.pdf, AMC_AppNote_015.pdf"
      },
      a_pos_too_cold: {
        answer: "**Slow response + large following error = gains too low, or current limit saturation.**\n\n**Fix:**\n1. **Increase position Kp** in 25% steps.\n2. If increasing Kp causes oscillation in the velocity loop, **tune velocity loop tighter first** — the position loop is wrapped around it.\n3. **Check current limit** — if the drive is current-saturated during motion, no amount of position-loop gain helps. Look at the current trace in the scope.\n4. For long moves at constant velocity, some **following error is normal** and scales with velocity ÷ Kp. Only fault if it exceeds the configured position-error limit.",
        source: "AMC_SW_Manual_ACE.pdf, AMC_AppNote_015.pdf"
      },
      a_pos_oscillates: {
        answer: "**Position oscillation around target = velocity loop is marginally stable, or integrator wind-up.**\n\n**Fix:**\n1. Drop out of position loop and **tune the velocity loop alone** using a scope step. If the velocity loop rings, the position loop will inherit that behavior amplified.\n2. Reduce **position Ki** — positional integrators are often unnecessary if velocity Ki already handles steady-state. Turn position Ki to zero and re-test.\n3. Check for **mechanical backlash** — gear or belt slop creates a dead zone the position loop fights, producing hunting.\n4. Verify **encoder resolution** is high enough for your target precision. Sub-count oscillation is inevitable if you're asking for resolution below encoder LSB.",
        source: "AMC_SW_Manual_ACE.pdf, AMC_AppNote_015.pdf"
      },
      a_pos_error_fault: {
        answer: "**Position-error fault during moves = motor can't follow the command.**\n\n**Fix:**\n1. Check the scope: is the **actual velocity saturated** during the move (flat-top at max velocity)? If yes, either raise the velocity limit or reduce commanded velocity.\n2. Check **current limit** during acceleration — current-saturation causes lag that accumulates into a position-error trip.\n3. Increase the **position-error fault threshold** if the current value is too strict for your application (some users use it more as a monitor than a hard fault).\n4. If the fault only trips on long moves, look at **following error = velocity ÷ Kp** — raise Kp or enable velocity feed-forward to reduce it.\n5. Verify **load inertia ratio** — reflected load >10× motor inertia will always lag unless gains are aggressive.",
        source: "AMC_SW_Manual_ACE.pdf, AMC_AppNote_015.pdf"
      },

      // --- Auto-tune shortcut ---
      a_tune_autotune: {
        answer: "**Auto-Tune workflow in ACE / DriveWare:**\n\n1. **Connect to the drive** and verify it's enabled without faults.\n2. **Enter motor parameters** — resistance (Rs), inductance (Ls), pole count, rated current, rated speed. Auto-tune reads these.\n3. **Clamp the rotor** for safety — auto-tune injects small current pulses that can cause unintended motion.\n4. Open **Tuning → Auto-Tune** (or \"Calculate Gains\" in some UIs).\n5. Run the current-loop tune first, verify the step response in the scope, then velocity, then position.\n6. **Save the gains to NVM** (Non-Volatile Memory) so they survive a power cycle.\n\nIf auto-tune fails, the blocker is usually: missing motor parameters, wrong commutation, an active fault, or a jammed rotor. See the individual loop branches for symptom-driven fixes.",
        source: "AMC_SW_Manual_ACE.pdf, AMC_AppNote_015.pdf, AMC_AppNote_014.pdf"
      },
    }
  },

  // =========================================================================
  // TREE 6: HOMING PROBLEMS
  // Sources: AppNote 062 (Hard Stop Homing, FlexPro CANOpen/Serial),
  // ACE Manual (homing methods), DigiFlex Serial comm manual (homing objects).
  // =========================================================================
  homing: {
    title: "Homing Problems",
    trigger_keywords: [
      "homing", "home position", "home switch", "hard stop home",
      "index pulse", "home offset", "home not found", "homing failed",
      "won't home", "can't home", "home timeout",
    ],
    root: "q_homing_method",
    nodes: {
      q_homing_method: {
        question: "Which homing method are you using?",
        options: [
          { label: "Home to hard stop (no switch or sensor)", next: "q_hardstop_symptom" },
          { label: "Home to a switch / limit input", next: "q_switch_home_symptom" },
          { label: "Home to index pulse (encoder Z)", next: "q_index_home_symptom" },
          { label: "Not sure which method — how do I pick?", next: "a_homing_method_picker" },
        ]
      },
      a_homing_method_picker: {
        answer: "**Picking a homing method** (CiA 402 / DS-402 standard):\n\n- **Hard Stop Home** — motor drives into a mechanical stop, current builds up past a threshold, drive calls that position home. Simple wiring, no sensor needed. Best for rigid stops; not safe for delicate mechanisms. See AN-062.\n- **Switch Home** (limit switch / proximity) — motor moves until a digital input fires. Fast and repeatable; requires wiring the switch.\n- **Index Home** — uses the encoder's Z / index pulse (one per revolution). Highest accuracy, but only works if your encoder has an index channel.\n- **Combo** — most applications use switch-to-approach + index-to-latch for both speed and precision.\n\nCiA 402 defines ~35 standard homing methods (negative/positive direction, switch + index, hard-stop + index, etc.). Your drive exposes them via object 0x6098 (Homing Method).",
        source: "AMC_AppNote_062.pdf, AMC_CommManual_FP_CANopen.pdf, AMC_CommManual_CANopen.pdf"
      },

      // --- Hard stop ---
      q_hardstop_symptom: {
        question: "What's happening with hard-stop homing?",
        options: [
          { label: "Drive never detects the stop — keeps pushing forever", next: "a_hardstop_threshold" },
          { label: "Faults with over-current before reaching the stop", next: "a_hardstop_overcurrent" },
          { label: "Detects stop but home position is off by random amount", next: "a_hardstop_offset" },
        ]
      },
      a_hardstop_threshold: {
        answer: "**Hard stop never detected = current threshold too high, or homing current limit too low.**\n\nHard-stop homing works by watching the current build up when the motor presses into the stop. If the threshold isn't met, the drive assumes it's still moving.\n\n**Fix (per AN-062):**\n1. **Lower the Homing Current Threshold** parameter until current while pressing reliably exceeds it. Start at 30–50% of continuous current rating.\n2. **Raise the Homing Current Limit** — if current is capped too low, the motor can't build enough torque to trip the threshold.\n3. **Increase the Homing Timeout** so the drive waits long enough for the current to ramp up against the stop.\n4. Scope **Current Feedback vs. Current Threshold** during the homing attempt to verify the curve actually crosses the threshold.\n5. Ensure the motor is **in velocity or current mode during homing**, not trying to track a position setpoint past the stop (which triggers a position-error fault first).",
        source: "AMC_AppNote_062.pdf p.2-4"
      },
      a_hardstop_overcurrent: {
        answer: "**Over-current fault before stop detection = homing profile is too aggressive for the mechanism.**\n\n**Fix:**\n1. **Reduce Homing Velocity** so the motor isn't slamming into the stop.\n2. **Reduce Homing Current Limit** so it caps below the drive's fault threshold (give yourself headroom).\n3. **Add a soft-stop deceleration** near the expected hard-stop position if your application allows approximating where the stop is.\n4. Check that the motor's **I²t thermal model** isn't tripping — long dwell at high current against a stop can trip the thermal fault even if you're below the peak current limit.",
        source: "AMC_AppNote_062.pdf, AMC_HWManual_FlexPro_PCB.pdf"
      },
      a_hardstop_offset: {
        answer: "**Home position drifts on hard stops = compliance or backlash in the mechanism.**\n\nEven a perfectly-working homing routine is only as accurate as the stop itself. If the stop flexes under load, every home is slightly different.\n\n**Fix:**\n1. **Back off and re-approach slowly** — most drives support a two-stage home: fast initial approach, then a slow final approach at lower current. Configure **Homing Offset** / **Homing Speed 2**.\n2. **Add an encoder index pulse** to the homing method — approach to hard-stop, then back off and latch on the next Z pulse. This removes hard-stop compliance from the final position.\n3. Check for **mechanical play / backlash** between the motor and the stop (couplings, belts, gears). A stiff coupling is worth the money here.\n4. Verify the **Homing Current Threshold** isn't so low that the motor is latching on friction rather than the stop itself.",
        source: "AMC_AppNote_062.pdf"
      },

      // --- Switch home ---
      q_switch_home_symptom: {
        question: "What's happening with switch homing?",
        options: [
          { label: "Drive doesn't see the switch (never homes)", next: "a_switch_not_seen" },
          { label: "Drive sees switch but homes to wrong side", next: "a_switch_wrong_side" },
          { label: "Homes OK but with variable accuracy", next: "a_switch_flakey" },
        ]
      },
      a_switch_not_seen: {
        answer: "**Drive doesn't detect the home switch.**\n\n**Fix:**\n1. **Verify the digital input is configured for Home** — in ACE / DriveWare, map the physical input pin to the Homing function. Unmapped inputs are ignored by the homing state machine.\n2. **Measure the input voltage** when the switch is pressed — it must swing above the drive's logic threshold (typically 10V for 24V-logic inputs). Check with a multimeter at the drive's I/O connector.\n3. Check **active-high vs active-low** configuration. An NPN sensor with the drive configured for PNP will look dead.\n4. Verify **input polarity / inversion** parameter.\n5. Test the input independently: in ACE, monitor the digital input state on the scope while you manually trip the switch. No state change = wiring/sensor problem, not a homing problem.",
        source: "AMC_SW_Manual_ACE.pdf, AMC_HWManual_FlexPro_PCB.pdf"
      },
      a_switch_wrong_side: {
        answer: "**Drive sees the switch but homes the wrong direction or side.**\n\nHoming methods in CiA 402 have direction and edge (rising/falling) encoded. Getting them wrong lands you on the opposite edge.\n\n**Fix:**\n1. **Check the Homing Method (object 0x6098)** against the CiA 402 table. Methods 1–14 differ in approach direction and which edge of the switch is used.\n2. If using a limit-switch-style method, verify your switch is actually a LIMIT switch (at travel end) vs a HOME switch (somewhere in middle of travel) — they use different methods.\n3. **Test direction in jog** before engaging the homer: if positive command drives the motor the wrong way, swap motor phases (or use the drive's direction-invert parameter) first.",
        source: "AMC_CommManual_FP_CANopen.pdf, AMC_AppNote_062.pdf"
      },
      a_switch_flakey: {
        answer: "**Homes OK but repeatability is poor = switch timing / hysteresis issue.**\n\n**Fix:**\n1. **Combine switch + index** — use a CiA 402 method that approaches the switch fast, then backs off and latches on the next encoder index pulse. The switch gets you close; the index pulse nails the position.\n2. **Slow down the final approach** — at high speed, switch debounce time + processor latency add ±several encoder counts of jitter.\n3. **Clean/replace mechanical switches** — contact bounce is a classic source of variable home position. Use a Hall-effect or optical proximity sensor for better repeatability.\n4. Check **switch mount rigidity** — a switch that flexes under impact moves the apparent home.",
        source: "AMC_AppNote_062.pdf, AMC_CommManual_CANopen.pdf"
      },

      // --- Index home ---
      q_index_home_symptom: {
        question: "What's happening with index-pulse homing?",
        options: [
          { label: "Never sees an index pulse", next: "a_index_not_seen" },
          { label: "Sees multiple index pulses, latches wrong one", next: "a_index_wrong_one" },
        ]
      },
      a_index_not_seen: {
        answer: "**No index pulse detected.**\n\n**Fix:**\n1. Confirm your encoder actually HAS an index (Z) channel — not all encoders do. Check the encoder datasheet and the drive's wiring diagram.\n2. Verify **Z+ / Z−** differential pair is wired to the drive's encoder connector (single-ended index lines often tie Z− to ground at the drive).\n3. In ACE, **scope the index signal** — one pulse per mechanical revolution. No pulse = wiring fault, damaged encoder, or encoder model without a Z channel.\n4. Some encoder modes (e.g., \"quadrature with commutation\" Hall-only mode) ignore the Z channel. Check **feedback configuration** — must be set to quadrature-with-index.\n5. If your motor makes >1 revolution before homing times out, there's no way the index is missing — it's either wired wrong or the feedback mode is wrong.",
        source: "AMC_AppNote_040.pdf, AMC_HWManual_FlexPro_PCB.pdf"
      },
      a_index_wrong_one: {
        answer: "**Multiple revolutions see multiple index pulses — which one is \"home\"?**\n\nWith an incremental encoder, every revolution produces one Z pulse. If your home range is >1 revolution, you need to pair index with a switch.\n\n**Fix:**\n1. Use a **home switch + index** combo homing method. The switch narrows down the region; the next index after the switch edge is the home.\n2. If you only have an index, restrict **travel to <1 revolution between power-on and home** — guarantees only one pulse will be seen.\n3. Consider switching to an **absolute encoder** (multi-turn or single-turn) if your application can't tolerate this limitation. See AN-040.",
        source: "AMC_AppNote_040.pdf, AMC_AppNote_062.pdf"
      },
    }
  },

  // =========================================================================
  // TREE 7: DRIVE WON'T CONNECT TO DRIVEWARE / ACE
  // Sources: ACE Manual, DriveWare Manual, AppNote 000 (DLL registration),
  // AppNote 001 (RS485 921K baud), AppNote 008 (RS232/485 interface).
  // =========================================================================
  software_connection: {
    title: "Drive Won't Connect to DriveWare / ACE / DriveLibrary",
    trigger_keywords: [
      "can't connect", "won't connect", "connection failed", "no connection",
      "driveware", "ace won't", "ace error", "dll registration",
      "com port", "usb not recognized", "driver", "firmware mismatch",
      "communication timeout", "drive not found", "drive not detected",
    ],
    root: "q_sw_symptom",
    nodes: {
      q_sw_symptom: {
        question: "How does the connection fail?",
        options: [
          { label: "Software can't find the COM / USB port at all", next: "a_sw_port_missing" },
          { label: "Finds port but 'drive not responding' / timeout", next: "q_sw_timeout_interface" },
          { label: "Connects then disconnects randomly", next: "a_sw_flaky" },
          { label: "'DLL registration' error dialog on startup", next: "a_sw_dll" },
          { label: "Firmware version mismatch warning", next: "a_sw_firmware" },
        ]
      },
      a_sw_port_missing: {
        answer: "**Software doesn't see the COM port.**\n\n**Fix:**\n1. **Windows Device Manager → Ports (COM & LPT)** — is the drive listed? If not, Windows isn't seeing the USB adapter. Check the cable and try a different USB port.\n2. **Install the USB-to-serial driver** — AMC drives typically need an FTDI or Silicon Labs CP210x driver depending on the model and adapter. Download from AMC's site or the chip vendor.\n3. If the COM port appears but the drive isn't reachable, note the port number and select it manually in ACE (some versions default to scanning only COM1–COM4).\n4. On macOS/Linux, drivers are usually kernel-native — check `ls /dev/tty.usbserial*` to confirm the device is enumerated.",
        source: "AMC_AppNote_008.pdf, AMC_SW_Manual_ACE.pdf"
      },
      q_sw_timeout_interface: {
        question: "Which interface are you using?",
        options: [
          { label: "RS-232 serial (9-pin D-sub)", next: "a_sw_rs232_timeout" },
          { label: "RS-485 serial (often via adapter)", next: "a_sw_rs485_timeout" },
          { label: "USB (built-in USB port on drive)", next: "a_sw_usb_timeout" },
          { label: "Ethernet / EtherCAT / CANopen", next: "a_sw_fieldbus_timeout" },
        ]
      },
      a_sw_rs232_timeout: {
        answer: "**RS-232 timeout** — common fixes:\n\n1. **Baud rate mismatch** — ACE / DriveWare default is 115200; some older drives default to 38400 or 9600. Try each baud rate, or check the drive's DIP switches / NVM for baud setting.\n2. **Cable pinout** — RS-232 requires a null-modem or straight-through cable depending on drive — verify against the drive's HW manual. Pin 2/3 swap (TX/RX) kills the link.\n3. **Address / Drive ID** — if the drive has a multi-drop address (even on RS-232), the software must target that address. Check NVM / DIP switches.\n4. **Ground loop** — RS-232 requires signal ground connection. Without it, the signal looks noisy and the UART desynchronizes.\n5. If it was working yesterday and broke today, check for a **loose connector** or a **power glitch** that reset the baud rate.",
        source: "AMC_AppNote_008.pdf, AMC_CommManual_RS485.pdf"
      },
      a_sw_rs485_timeout: {
        answer: "**RS-485 timeout** — common fixes:\n\n1. **Termination** — the last device on the RS-485 bus needs a 120 Ω terminator. Missing termination causes reflections and silent drops.\n2. **Baud rate** — AN-001 covers 921.6 kbaud operation specifically for AMC drives; non-standard baud rates often fail if the adapter/driver doesn't support them. Start at 115200 and work up.\n3. **Half-duplex vs full-duplex** — RS-485 is half-duplex by default. Your adapter must match the drive's mode. Full-duplex (4-wire) drives wired as half-duplex (2-wire) won't talk.\n4. **Bias resistors** — on a non-terminated bus, you may need 680 Ω pull-up / pull-down biasing to keep the differential line at a known idle state.\n5. **Drive address (node ID)** — multi-drop requires each drive to have a unique address and the master must query that address.\n6. Check **ground reference** — RS-485 is differential but needs a common ground reference for the receivers to work.",
        source: "AMC_AppNote_001.pdf, AMC_AppNote_008.pdf, AMC_CommManual_RS485.pdf"
      },
      a_sw_usb_timeout: {
        answer: "**USB timeout** — built-in USB ports are usually USB-to-serial bridges internally:\n\n1. **Check COM port number** — the drive enumerates as a virtual COM port. Device Manager shows the number; select that exact one in ACE.\n2. **Baud rate** — even USB drives use serial framing internally; baud mismatch causes timeouts.\n3. **Driver version** — mismatched FTDI/CP210x driver versions can cause packet loss. Update to the latest from the chip vendor.\n4. **USB hub issues** — plug directly into a computer port, skip USB hubs (especially bus-powered ones). Some USB 3.0 ports also cause problems — try a USB 2.0 port.\n5. **Power** — if the drive is unpowered, the USB port can enumerate but not respond. Verify DC power to the drive first.",
        source: "AMC_SW_Manual_ACE.pdf, AMC_AppNote_008.pdf"
      },
      a_sw_fieldbus_timeout: {
        answer: "**Fieldbus timeout (EtherCAT / CANopen / Ethernet)** — use the Communication Error tree for detailed diagnosis. Quick checks:\n\n1. **Physical link** — LED on drive's network port should indicate link state (green = connected, off = no link). Check cable.\n2. **Node / slave address** — must match the master's expected address.\n3. **ESI / EDS file** — on EtherCAT, the master needs the drive's ESI XML file loaded. On CANopen, it needs the EDS file.\n4. **Cycle time** — set too fast, the drive can't respond in time. 1 kHz (1 ms) is a safe starting cycle.\n5. **Firmware** — drive and master must share a compatible protocol version.",
        source: "AMC_CommManual_FP_EtherCAT.pdf, AMC_CommManual_FP_CANopen.pdf"
      },
      a_sw_flaky: {
        answer: "**Connects then drops randomly** — usually noise or power:\n\n1. **Ground loop** — the PC and the drive share a ground path that's carrying power-supply noise. Use an isolated USB-to-serial adapter.\n2. **Cable length** — RS-232 limits to ~15 meters; RS-485 to ~1200 meters at low baud but less at high baud. Long cables + high baud = errors.\n3. **EMI** — route signal cable away from motor power cables. Use shielded cable, grounded at drive end only. See AN-023 on ferrite cores.\n4. **USB power saving** — Windows' \"USB selective suspend\" disables the adapter after idle. Device Manager → USB Root Hub → Power Management → uncheck \"Allow the computer to turn off this device.\"\n5. **Drive reset** — if the drive is power-cycling silently (e.g., brown-outs on the DC bus), the link drops on every reset. Check DC bus voltage stability under load.",
        source: "AMC_AppNote_023.pdf, AMC_SW_Manual_ACE.pdf"
      },
      a_sw_dll: {
        answer: "**DLL registration error (AN-000)** — this is a Windows issue, not a drive issue.\n\n**Fix (per AN-000):**\n1. **Run ACE / DriveWare as Administrator** — right-click the shortcut, \"Run as administrator.\" The installer may have failed to register DLLs without admin rights.\n2. **Re-install the software** — an interrupted install leaves DLLs on disk but not registered. Uninstall fully, reboot, re-install as administrator.\n3. **Manually re-register** (advanced): open Command Prompt as admin, navigate to the install folder, run `regsvr32 <dllname>.dll` for each flagged DLL.\n4. On Windows 10/11, **Controlled Folder Access** (ransomware protection) can block DLL registration. Temporarily disable or add the install folder as an allowed app.\n5. On a corporate-managed machine, ask IT — software-restriction policies often block COM DLL registration.",
        source: "AMC_AppNote_000.pdf"
      },
      a_sw_firmware: {
        answer: "**Firmware version mismatch** — the software is built for a specific firmware range.\n\n**Fix:**\n1. **Check what firmware is on the drive** — ACE displays it on the connection dialog. Write it down.\n2. **Check the ACE / DriveWare release notes** for the supported firmware range.\n3. **Update ACE / DriveWare** to the latest version first — newer software supports a wider range of firmware.\n4. If the drive has **older firmware** and your ACE is too new, you can either (a) update drive firmware (AMC-provided `.flc` file via ACE's firmware-update dialog), or (b) roll back ACE to a matching version.\n5. **NEVER update firmware without the drive-specific file** — each drive family has its own firmware. Flashing the wrong family bricks the drive.\n6. After a firmware update, expect to **re-tune** — gains often don't carry across versions.",
        source: "AMC_SW_Manual_ACE.pdf, AMC_SW_ReleaseNotes_ACE.pdf"
      },
    }
  },

  // =========================================================================
  // TREE 8: SAFE TORQUE OFF (STO) ISSUES — FlexPro safety circuit
  // Sources: Compliance Safety STO FlexPro PDF, FlexPro HW manual.
  // =========================================================================
  sto_safety: {
    title: "Safe Torque Off (STO) Problems",
    trigger_keywords: [
      "sto", "safe torque off", "safety input", "safety circuit",
      "safety function", "sto fault", "sto not working", "sil 3",
      "pld", "safety relay",
    ],
    root: "q_sto_symptom",
    nodes: {
      q_sto_symptom: {
        question: "What's happening with STO?",
        options: [
          { label: "Drive reports STO active when it shouldn't be", next: "a_sto_false_trigger" },
          { label: "Drive doesn't disable when STO is asserted", next: "a_sto_doesnt_trigger" },
          { label: "STO inputs and wiring — how do I connect them?", next: "a_sto_wiring" },
          { label: "Drive faults on STO discrepancy", next: "a_sto_discrepancy" },
        ]
      },
      a_sto_wiring: {
        answer: "**STO wiring on FlexPro drives:**\n\n1. STO uses **two independent 24 V inputs** (STO1 and STO2). Both must be energized (high) to enable the drive. If either drops low, the drive goes to Safe Torque Off state.\n2. Supply each STO input from a **certified safety relay or safety controller** — standard PLC outputs are not SIL-rated.\n3. **Do not tie STO1 and STO2 together** — the redundancy is the entire safety function. Tying them makes the safety certification invalid.\n4. **Test pulses** — most safety controllers pulse the STO inputs briefly to test the diagnostic coverage. FlexPro inputs tolerate pulses up to the duration specified in the Compliance STO document; longer pulses trip the drive.\n5. See the dedicated **AMC_Compliance_Safety_STO_FlexPro.pdf** for certified wiring diagrams, response times, and SIL 3 / PL e certification statements.",
        source: "AMC_Compliance_Safety_STO_FlexPro.pdf, AMC_HWManual_FlexPro_PCB.pdf"
      },
      a_sto_false_trigger: {
        answer: "**STO reports active when it shouldn't** — the safety circuit is seeing a low on at least one input.\n\n**Fix:**\n1. **Measure both STO1 and STO2 inputs with a multimeter** while the safety loop is \"safe\". Both must read 24 V DC relative to the input common. If one is low, the wiring from the safety controller is the problem.\n2. Check the **safety relay's output contacts** — oxidation or a worn relay can drop voltage.\n3. **Wiring corrosion / loose terminals** — STO inputs are low-current, so a high-resistance joint shows up as a low voltage on the input even though there's no visible fault.\n4. Check for **inductive spikes** on the STO line from nearby E-stop or contactor coils. Add a flyback diode on the coil.\n5. **Ground reference** — STO inputs reference 0 V common. If the common isn't tied between the safety source and the drive, levels appear wrong.",
        source: "AMC_Compliance_Safety_STO_FlexPro.pdf"
      },
      a_sto_doesnt_trigger: {
        answer: "**Drive doesn't disable when STO is asserted** — this is a safety-critical failure. Stop using the machine.\n\n**Mandatory checks:**\n1. **Are you using a drive variant that HAS STO?** Not every FlexPro model includes the STO option. Check the SKU against the compliance documentation.\n2. **Are the STO inputs wired?** An unwired STO input floats or pulls up to 24 V internally (manufacturer-dependent) — defeating the safety function.\n3. **Jumpers installed by factory or previous integrator** — some drives ship with STO jumped out for test. Remove the jumper and wire the real safety circuit.\n4. **Contact AMC technical support** — this is not a field-troubleshoot issue. STO failures can be a defect or a misunderstanding of the certified configuration.\n\n**Do not commission the safety function until it has been verified by the safety integrator.** The drive's STO is only one link in a certified chain — the full chain must be validated per ISO 13849-2.",
        source: "AMC_Compliance_Safety_STO_FlexPro.pdf"
      },
      a_sto_discrepancy: {
        answer: "**STO discrepancy fault = STO1 and STO2 mismatch for longer than the tolerance window.**\n\nBoth inputs must transition in sync; if one goes low and the other stays high (or vice versa) for more than the drive's discrepancy-detection time, it faults.\n\n**Fix:**\n1. **Simultaneous switching** — drive both inputs from the same safety relay's redundant contacts, not from separate PLC channels with different timing.\n2. **Test-pulse timing** — if your safety controller pulse-tests, the pulses on STO1 and STO2 must be staggered in time so they don't both go low simultaneously, or within the drive's tolerance window. See AMC_Compliance_Safety_STO_FlexPro.pdf for the exact timing.\n3. **Wiring asymmetry** — if one input's cable is much longer than the other, propagation delay alone can trigger discrepancy at fast cycle times. Keep cable lengths similar.\n4. **Clearing the fault** — discrepancy faults are usually latched; toggle both STO inputs through a known-good safe state, then back to normal, to clear.",
        source: "AMC_Compliance_Safety_STO_FlexPro.pdf"
      },
    }
  },

  // =========================================================================
  // TREE 9: ENCODER / FEEDBACK CONFIGURATION
  // Sources: AppNote 014 (motor phasing/commutation), AppNote 040 (absolute
  // feedback), AppNote 041 (sinusoidal commutation speed limits), HW manuals.
  // =========================================================================
  encoder_feedback: {
    title: "Encoder / Feedback Configuration",
    trigger_keywords: [
      "encoder", "feedback", "hall sensor", "hall", "commutation",
      "sin/cos", "sincos", "absolute encoder", "incremental encoder",
      "bissc", "biss-c", "endat", "ssi", "resolver",
      "encoder setup", "feedback wrong direction",
    ],
    root: "q_feedback_type",
    nodes: {
      q_feedback_type: {
        question: "What type of feedback are you setting up or troubleshooting?",
        options: [
          { label: "Incremental encoder (quadrature A/B, optional index)", next: "q_inc_enc_symptom" },
          { label: "Absolute encoder (BiSS-C, EnDat, SSI)", next: "q_abs_enc_symptom" },
          { label: "Hall sensors only (no encoder)", next: "q_halls_symptom" },
          { label: "Resolver", next: "a_fb_resolver" },
          { label: "Sin/Cos encoder", next: "a_fb_sincos" },
          { label: "Motor runs wrong direction / torque doesn't match command", next: "a_fb_commutation" },
        ]
      },

      q_inc_enc_symptom: {
        question: "Incremental encoder issue:",
        options: [
          { label: "Position counts the wrong way", next: "a_inc_direction" },
          { label: "Position skips or jumps randomly", next: "a_encoder_noise" },
          { label: "Drive faults on feedback loss", next: "a_inc_lost_signal" },
          { label: "Index pulse not detected", next: "a_index_not_seen" },
        ]
      },
      a_inc_direction: {
        answer: "**Encoder counts the wrong direction.**\n\n**Fix:**\n1. **Swap A and B channels** — this reverses the decoded direction.\n2. **Or set the encoder-direction-invert parameter** in ACE / DriveWare if your drive exposes one.\n3. **Or swap two motor phases** — reverses the physical rotation direction to match the encoder, not the other way around. Pick one method; don't swap both or you'll undo it.\n4. After fixing direction, **re-run commutation** if your motor uses Hall-based or phase-detect commutation — the electrical angle reference may be inverted.",
        source: "AMC_AppNote_014.pdf, AMC_HWManual_FlexPro_PCB.pdf"
      },
      a_inc_lost_signal: {
        answer: "**Feedback-loss fault on incremental encoder.**\n\n**Fix:**\n1. **Check differential pairs** — A+/A−, B+/B−, Z+/Z−. Drives with feedback-loss detection watch for both halves of the pair swinging opposite; single-ended wiring trips the fault.\n2. **Cable shielding** — shield grounded only at drive end.\n3. **5 V supply** — verify +5 V is reaching the encoder. Long cables drop voltage; some drives have a Kelvin (sense) pair to compensate.\n4. **Cable damage** — if the fault is intermittent with machine motion, the cable may be chafed or flex-fatigued. Replace suspect cable.\n5. **Encoder failure** — at this point, substitute a known-good encoder to isolate.",
        source: "AMC_HWManual_FlexPro_PCB.pdf, AMC_AppNote_040.pdf"
      },

      q_abs_enc_symptom: {
        question: "Absolute-encoder issue:",
        options: [
          { label: "Drive can't read the encoder at all", next: "a_abs_no_comm" },
          { label: "Reads position but it's wrong / drifts / wraps oddly", next: "a_abs_wrong_position" },
          { label: "How do I configure / enable an absolute encoder?", next: "a_abs_setup" },
        ]
      },
      a_abs_setup: {
        answer: "**Absolute encoder setup (per AN-040 for DigiFlex Performance):**\n\n1. **Select the encoder protocol** in ACE / DriveWare — BiSS-C, EnDat 2.1/2.2, or SSI. Protocol is encoder-specific; check the encoder's datasheet.\n2. **Set the position resolution** — single-turn bits + multi-turn bits. The drive needs to know how many bits of each to properly decode.\n3. **Wire the data + clock lines** — absolute encoders are serial. Check for correct TX / RX / CLK / GND pairs and differential pair polarity.\n4. **Supply voltage** — BiSS-C / EnDat usually run on 5 V; some on 8–15 V. Wrong supply fries the encoder.\n5. **Set the commutation offset** after mechanical installation — the drive needs to know the electrical angle at encoder position zero. Run auto-commutation or enter the offset manually.\n6. **Save to NVM** so the settings persist across power cycles.",
        source: "AMC_AppNote_040.pdf"
      },
      a_abs_no_comm: {
        answer: "**Absolute encoder not communicating.**\n\n**Fix:**\n1. **Protocol mismatch** — confirm the drive is set for the exact protocol the encoder uses (BiSS-C vs BiSS-B, EnDat 2.1 vs 2.2, SSI Gray vs Binary).\n2. **Pinout verification** — absolute encoders often share the same connector form factor as incremental but with very different pin assignments. Verify against both datasheets.\n3. **Termination / biasing** — some protocols require line termination at the drive end. Check the HW manual.\n4. **Clock speed** — too fast a clock for a long cable fails. Start at the lowest supported speed and work up.\n5. **Encoder supply** — measure the actual voltage at the encoder end of the cable, not at the drive. Long cables drop V significantly.",
        source: "AMC_AppNote_040.pdf, AMC_HWManual_FlexPro_PCB.pdf"
      },
      a_abs_wrong_position: {
        answer: "**Absolute position reads but is wrong.**\n\n**Fix:**\n1. **Resolution mismatch** — if you told the drive 17-bit single-turn but the encoder is 13-bit, the upper bits will be noise. Verify the encoder's exact bit-count from its datasheet.\n2. **Gray ↔ Binary** — SSI encoders output Gray code; binary-configured readers show garbage when rotated. Set the encoder-code parameter correctly.\n3. **Multi-turn wrap** — a 12-bit multi-turn counter wraps at 4096 revs. If your application requires more range, you need a higher-count encoder or a battery-backed multi-turn.\n4. **Commutation offset** — position may be read correctly but electrically referenced wrong, causing torque-direction flips. Re-run auto-commutation.\n5. **Offset parameter** — check if the drive has an encoder-offset parameter accidentally set to a non-zero value.",
        source: "AMC_AppNote_040.pdf"
      },

      q_halls_symptom: {
        question: "Hall-sensor issue:",
        options: [
          { label: "Valid Hall state fault (000 or 111)", next: "a_analog_hall_fault" },
          { label: "Motor turns in wrong direction on first command", next: "a_hall_direction" },
          { label: "Motor cogs or doesn't commutate smoothly", next: "a_fb_commutation" },
        ]
      },
      a_hall_direction: {
        answer: "**Motor turns backward on first commutation.**\n\nHall wiring and motor phase wiring must BOTH be correct for torque direction to match command.\n\n**Fix:**\n1. **Swap any two motor phases** (U↔V, V↔W, or U↔W) — this reverses the motor direction without touching the encoder or Halls.\n2. **Or swap two of the three Hall sensor wires** — changes the electrical-angle decoding.\n3. **Or run auto-commutation** — the drive will figure out the correct phase order automatically (requires the motor to be free to rotate).\n4. Pick ONE method. Swapping both motor phases AND Halls cancels out.\n5. Reference AN-014's motor-phasing procedure for manual verification with a scope.",
        source: "AMC_AppNote_014.pdf"
      },

      // --- Universal commutation answer ---
      a_fb_commutation: {
        answer: "**Commutation fault** — the drive doesn't know the correct electrical angle of the rotor.\n\n**Fix:**\n1. **Run auto-commutation** in ACE (Tuning → Commutation → Auto-Commute). The motor must be free to rotate and the current loop must be tuned first.\n2. For **manual commutation**, follow AN-014's procedure: apply a small DC current to one phase pair and observe which Hall state activates. Record the alignment between Hall states and phase order.\n3. **Commutation type mismatch**: verify the drive is set for sinusoidal vs. trapezoidal vs. Hall-only matching your motor. Trapezoidal-commutated sinusoidal-wound motors produce 15% torque ripple.\n4. **Pole count wrong**: the drive must know the motor's electrical-pole count. If set wrong, commutation angle wraps at the wrong rate.\n5. See AN-041 for sinusoidal-commutation speed limits — at very high speeds, sinusoidal can lose sync and fall back to block commutation with a torque-ripple bump.",
        source: "AMC_AppNote_014.pdf, AMC_AppNote_041.pdf"
      },

      a_fb_resolver: {
        answer: "**Resolver setup** on AMC drives.\n\n1. **Verify transformation ratio** — the drive expects a specific transformation ratio (typically **0.5 Vrms** on DigiFlex Performance resolver drives). Check the drive's datasheet before installation. A mismatched resolver produces degraded signals and the drive may fail feedback checks. See HW manual page 54 (DigiFlex Panel CANopen) and equivalent.\n2. **Excitation voltage and frequency** — typical AMC setup is 4 Vrms at 5 kHz. The drive outputs this to the resolver primary winding; wrong voltage = wrong feedback amplitude.\n3. **Wiring**: 6 wires (excitation pair + two secondary pairs for sine and cosine). Differential pairs must be wired polarity-correct.\n4. **Shielding and twist**: shield the resolver cable and twist each differential pair to reject noise.\n5. **Pole-pair count** — tell the drive how many resolver electrical cycles per mechanical revolution. Standard industrial resolvers are 1-speed (one electrical cycle per mechanical rev); multi-speed resolvers scale up.\n6. If your resolver's transformation ratio doesn't match the drive's expected ratio, either change resolvers or use a different drive — most AMC resolver drives don't configure this parameter in software.",
        source: "AMC_HWManual_DigiFlex_Panel_CANopen.pdf p.54, AMC_HWManual_DigiFlex_Panel_RS485-ModbusRTU.pdf p.27, AMC_SW_Manual_DriveWare.pdf p.36"
      },

      a_fb_sincos: {
        answer: "**Sin/Cos encoder setup.**\n\n1. Sin/Cos encoders output analog differential sine and cosine signals, typically **1 Vpp** peak-to-peak. Not compatible with drive inputs expecting digital quadrature.\n2. **Verify the drive has a Sin/Cos option** — it's a hardware feature, not a software mode. Check the drive's datasheet / HW manual.\n3. **Interpolation** — the drive interpolates between counts for sub-count resolution. Configure the interpolation factor in software (typical 4096x).\n4. **Signal amplitude calibration** — Sin/Cos drives expect 1 Vpp centered on 2.5 V. If signal amplitudes don't match, the drive's interpolation produces non-uniform counts. Scope the signals and adjust encoder supply if necessary.\n5. **Index / reference mark** — most Sin/Cos encoders have a separate TTL index line for homing.\n6. See AN-040 for configuration details on DigiFlex Performance.",
        source: "AMC_AppNote_040.pdf, AMC_HWManual_DigiFlex_Panel_EtherCAT.pdf"
      },
    }
  },

  // =========================================================================
  // TREE 10: THERMAL / OVERHEATING
  // Sources: HW manuals (thermal ratings, derating curves), AN-009 (red LED
  // overtemp), AN-003 (current foldback envelope), datasheets.
  // =========================================================================
  thermal: {
    title: "Thermal / Overheating Issues",
    trigger_keywords: [
      "overtemperature", "over-temperature", "over temp", "overtemp",
      "too hot", "heatsink hot", "drive hot", "motor hot",
      "thermal fault", "thermal shutdown", "foldback", "i2t",
      "I²t", "current foldback", "derating", "derate",
      "drive is hot", "motor is hot", "overheating",
    ],
    root: "q_thermal_what",
    nodes: {
      q_thermal_what: {
        question: "What's hot — and how did you find out?",
        options: [
          { label: "Drive reports over-temperature fault (LED or status)", next: "q_drive_overtemp_when" },
          { label: "Motor housing is hot to the touch", next: "a_motor_hot" },
          { label: "Drive's heatsink is hot but no fault yet", next: "a_heatsink_warm" },
          { label: "Current foldback is limiting my output (I²t)", next: "a_current_foldback" },
        ]
      },
      q_drive_overtemp_when: {
        question: "When does the over-temperature fault happen?",
        options: [
          { label: "Immediately at power-up (before any motion)", next: "a_overtemp_immediate" },
          { label: "After running for a while at high current", next: "a_overtemp_after_run" },
          { label: "Only in hot ambient conditions (summer, cabinet)", next: "a_overtemp_ambient" },
          { label: "During / after regenerative braking", next: "a_overtemp_regen" },
        ]
      },
      a_overtemp_immediate: {
        answer: "**Over-temp fault at power-up = stuck sensor, damaged drive, or wrong thermal configuration.**\n\n**Fix:**\n1. **Measure actual heatsink temperature** with an IR thermometer or thermocouple. If it's under 40 °C / 104 °F and the drive says overtemp, the **thermistor on the heatsink may be damaged / disconnected**.\n2. **Check motor thermistor wiring** — if you've configured the drive to use a motor-mounted thermistor (PTC or NTC), an open or shorted thermistor wire reads as maximum temperature. Disconnect the motor thermistor temporarily; if the fault clears, the wiring or sensor is the issue.\n3. **Check thermistor configuration** — some drives let you enable/disable the motor thermistor input and set active-closed vs active-open. A mis-configured sensor is always \"hot\" from the drive's perspective. See CommManual CANopen p.153 on thermistor / thermal cutoff switch configuration.\n4. If measured heatsink temp is genuinely high at power-on, the drive was probably hot from the previous run and hasn't cooled. Wait 10–15 min.",
        source: "AMC_CommManual_CANopen.pdf p.153, AMC_AppNote_009.pdf p.2"
      },
      a_overtemp_after_run: {
        answer: "**Over-temp after sustained operation = drive is genuinely exceeding its thermal limit. Approximately 65–85 °C internal limit depending on drive family.**\n\n**Fix (root causes in order):**\n1. **Heatsink / thermal interface** — check that the drive is **mounted to a heat-transferring surface** per its HW manual. Without the specified heatsink or thermal pad, the drive can't dissipate heat. PCB-mount drives typically need the specified cold plate or ground plane.\n2. **Thermal compound** — if the drive uses a thermal pad or grease against a heatsink, it must be clean and correctly applied. Dried grease or a missing pad cuts thermal conductivity dramatically.\n3. **Continuous current too high for your duty cycle** — if you're running closer to the drive's continuous rating, even slight thermal limits will trip. Derate per the datasheet's temperature curve (datasheets show continuous-current vs ambient-temperature curves).\n4. **Airflow** — if the drive is in an enclosed cabinet, forced airflow (fan) may be required. Spec sheets call out required airflow in CFM.\n5. **The drive will auto-recover** once internal temperature drops below the reset threshold (typically 10 °C hysteresis).",
        source: "AMC_HWManual_FlexPro_PCB.pdf, AMC_HWManual_DigiFlex_Panel_CANopen.pdf, AMC_AppNote_009.pdf"
      },
      a_overtemp_ambient: {
        answer: "**Overtemp only in hot ambient = you're outside the drive's derated operating range.**\n\nEvery AMC drive has a **derating curve** in its datasheet that reduces allowable continuous current as ambient temperature rises. Typical spec: full rating to ~40 °C, linear derate to 50% at 65 °C.\n\n**Fix:**\n1. **Check the derating curve** on your drive's datasheet. Compare your actual cabinet temperature (measured near the drive) to the allowed continuous current at that temperature.\n2. **Add cabinet cooling** — a small fan or AC unit often solves this. Target 40 °C or below at the drive's intake.\n3. **Move the drive** to a cooler location if the cabinet can't be cooled.\n4. **Upgrade to a higher-rated drive** — a drive rated for more continuous current will derate less aggressively at your ambient.\n5. **Duty cycle the application** — if loading is intermittent, the drive's thermal mass can absorb the duty cycle without hitting the limit. I²t integration runs on a time constant of minutes.",
        source: "AMC_Datasheet_FE060-25-EM.pdf p.3, AMC_HWManual_FlexPro_PCB.pdf"
      },
      a_overtemp_regen: {
        answer: "**Overtemp during/after braking = regen energy is dumping into the drive's internal shunt.**\n\nWhen the motor decelerates, kinetic energy flows back into the DC bus. The drive's **internal shunt regulator** (if present) dissipates this as heat to keep bus voltage in range. Heavy regen cycles overheat the shunt resistor.\n\n**Fix:**\n1. **Add an external shunt resistor** — see the regen/shunt tree. External shunts dissipate the energy outside the drive and won't trigger overtemp.\n2. **Reduce deceleration rate** — slower decel means less peak regen power. Often effective for high-inertia loads.\n3. **Check if your drive even has an internal shunt** — small drives often don't, and the DC bus pumps up until the overvoltage fault trips. If that's your symptom instead of overtemp, see the regen tree.\n4. **Use a larger DC bus capacitor** to absorb transient energy (limited effect but helps with short regen bursts).",
        source: "AMC_AppNote_009.pdf, AMC_HWManual_FlexPro_PCB.pdf"
      },
      a_motor_hot: {
        answer: "**Motor housing running hot.**\n\nSome heat is normal — a servo motor at continuous rated torque typically stabilizes at 80–100 °C case temperature. Class F / H insulation allows 155–180 °C rotor.\n\n**Check if it's actually too hot:**\n1. **Read the motor's max allowed temperature** from its nameplate or datasheet. This is usually case or winding temperature.\n2. **Measure actual winding temp** via the motor's thermistor (if installed) or an IR gun on the case.\n3. If you're **at or below rated current** and the motor is at its rated thermal limit — that's normal. Not a drive issue.\n4. If you're **below rated current** and the motor is overheating:\n   - Wrong motor parameters entered (Kt, inertia) cause the drive to request more current than necessary\n   - Bad commutation causes the drive to waste current as heating instead of torque (run auto-commutation)\n   - Motor shaft binding — load is fighting the motor\n   - Cooling fan not running (if the motor has one)\n\n**The drive's I²t model may be protective** — foldback will kick in to keep the motor from cooking. See the current foldback branch.",
        source: "AMC_AppNote_003.pdf, AMC_AppNote_014.pdf"
      },
      a_heatsink_warm: {
        answer: "**Warm heatsink, no fault yet = normal operation near the drive's capacity.**\n\nA drive under continuous load dissipates watts of heat; the heatsink is supposed to be warm. The drive self-protects at 65–85 °C internal; you'll get a fault before damage.\n\n**When to worry:**\n1. If the heatsink is approaching **too hot to touch for 2 seconds** (~60 °C surface) AND you're at rated load, you're near the thermal limit. Add cooling headroom.\n2. If the heatsink is warm but you're only at 30% of rated current, something's wrong: wrong motor parameters, regen dumping into internal shunt, or a mechanical binding.\n3. **Preventive fixes:** add a fan, upsize the drive, derate your current command, or duty-cycle the load.\n\n**Bottom line:** warm-but-not-faulting is fine. Track the heatsink temperature over time during your duty cycle; if it climbs steadily toward the fault threshold, intervene before the fault happens.",
        source: "AMC_HWManual_FlexPro_PCB.pdf"
      },
      a_current_foldback: {
        answer: "**Current foldback is the drive protecting your motor (or itself) from thermal damage via I²t.**\n\nThe drive integrates I² × time and compares it to a limit. When that integral exceeds the threshold, the drive **reduces output current** to prevent continuous overheating. Behavior:\n- Short high-current bursts (peak) are allowed\n- Sustained high current trips foldback\n- Output current limits to the continuous rating until the integral bleeds down\n\n**Per AN-003**, the typical foldback envelope on DigiFlex drives is: peak current for ~2 seconds, then linear rolloff to continuous over ~10 seconds.\n\n**Fix if foldback is hurting your application:**\n1. **Verify the peak/continuous current settings** match your motor. If peak is set to 100 A but your motor can only handle 50 A, you're thermally overloading the MOTOR even though the drive is happy.\n2. **Lower your acceleration** — aggressive accel demands peak current. Slower ramps stay below the peak region and avoid foldback.\n3. **Lower your continuous command** — if the application truly needs less current on average, the foldback won't kick in.\n4. **Upsize the drive / motor** — if your application requires sustained high current and foldback keeps kicking in, the hardware is undersized.\n5. **Check motor thermistor** — if the drive is also reading motor-side thermal data, foldback triggers on motor temp too.",
        source: "AMC_AppNote_003.pdf, AMC_CommManual_CANopen.pdf p.288"
      },
    }
  },

  // =========================================================================
  // TREE 11: REGEN / SHUNT REGULATOR ISSUES
  // Sources: AN-009 (over-voltage from regen), HW manuals (shunt specs),
  // datasheets (bus overvoltage thresholds).
  // =========================================================================
  regen_shunt: {
    title: "Regen / Shunt Regulator Problems",
    trigger_keywords: [
      "regen", "regenerative", "shunt", "shunt regulator", "shunt resistor",
      "bus overvoltage", "dc bus voltage rise", "back emf",
      "deceleration fault", "braking resistor", "external regen",
      "vertical load", "overhauling load",
    ],
    root: "q_regen_symptom",
    nodes: {
      q_regen_symptom: {
        question: "What's the regen-related symptom?",
        options: [
          { label: "Overvoltage fault during deceleration", next: "a_regen_overvoltage_decel" },
          { label: "Drive's internal shunt resistor gets hot (no external added)", next: "a_regen_internal_shunt" },
          { label: "Power LED flashes red/green during motion", next: "a_regen_led_flash" },
          { label: "Need to spec an external shunt/brake resistor", next: "a_regen_spec_external" },
          { label: "Vertical or overhauling load — how do I handle regen?", next: "a_regen_vertical" },
        ]
      },
      a_regen_overvoltage_decel: {
        answer: "**Overvoltage fault on deceleration = regen energy exceeding the drive's ability to absorb it.**\n\nDuring decel, the motor acts as a generator. Energy flows back into the DC bus, pumping up the voltage. When bus exceeds the drive's OV threshold (typically ~110% of nominal max), the drive trips to protect itself.\n\n**Fix (in order of cost):**\n1. **Reduce deceleration rate** — halves the peak regen power. Often the simplest fix.\n2. **Add S-curve profiling** — softens the deceleration transition, spreads regen power over more time.\n3. **Add an external shunt resistor** — dissipates regen energy as heat outside the drive. See the external-shunt spec branch.\n4. **Larger DC bus capacitance** — helps with short bursts but doesn't solve sustained regen.\n5. **Lower the nominal bus voltage** — gives the drive more headroom before OV trip. Trade-off: reduces max motor speed.\n6. **For vertical loads**, an external shunt is usually mandatory — gravity keeps pumping energy back whenever you decelerate a descending load.",
        source: "AMC_AppNote_009.pdf, AMC_HWManual_FlexPro_PCB.pdf"
      },
      a_regen_internal_shunt: {
        answer: "**Internal shunt getting hot = drive is working hard to absorb regen, dissipating it as heat inside the drive.**\n\nMany AMC drives have a small internal shunt resistor that handles moderate regen. Heavy or frequent regen saturates it.\n\n**Fix:**\n1. **Add an external shunt** — wires to the drive's shunt-out terminals (check HW manual for exact terminal names, usually P+/P−/R or similar). External shunts can dissipate 10× what the internal one handles.\n2. **External shunt sizing**: calculate peak regen power and average regen power for your application. Peak = ½ × J × ω² / decel_time. Average = peak × (decel_time / cycle_time). Pick a resistor rated above both.\n3. **Resistor value**: typically **bus voltage² / peak power**. For a 60 V drive regen'ing 2 kW peak: R ≈ 60² / 2000 = 1.8 Ω. Err on the lower side (higher current dissipation) within drive spec.\n4. **Confirm the drive supports external shunts** — not all AMC drives expose shunt terminals. Check the datasheet.",
        source: "AMC_HWManual_FlexPro_PCB.pdf, AMC_HWManual_DigiFlex_Panel_CANopen.pdf"
      },
      a_regen_led_flash: {
        answer: "**Power LED flashing red/green during motion = shunt regulator is actively dissipating regen energy.**\n\n**Per the HW manual, this is often NORMAL behavior** during deceleration — it just means the drive is clamping bus voltage. It does not indicate a fault.\n\n**When to worry:**\n1. **Constant flashing** during all motion (not just decel) — suggests something else is pumping energy back, like an overhauling load or a gravity-pulled axis.\n2. **Flashing becomes solid red** — the internal shunt overheated or failed. Needs external shunt or reduced regen.\n3. **Flashing + overvoltage fault** — internal shunt isn't keeping up. External shunt required.\n\nIf you just see brief flashes on deceleration and no faults, there's nothing to fix.",
        source: "AMC_HWManual_DigiFlex_Panel_EtherCAT.pdf p.48"
      },
      a_regen_spec_external: {
        answer: "**Sizing an external shunt resistor:**\n\n**Step 1 — Calculate peak regen power (watts):**\n    P_peak = ½ × J_total × ω² / t_decel\n\nWhere J_total is total reflected inertia (kg·m²), ω is velocity at start of decel (rad/s), t_decel is decel time (s).\n\n**Step 2 — Calculate average regen power (watts):**\n    P_avg = P_peak × (t_decel / t_cycle)\n\nWhere t_cycle is the full cycle time (one complete regen event every t_cycle seconds).\n\n**Step 3 — Pick resistor value:**\n    R = V_shunt² / P_peak  (V_shunt ≈ 108% of nominal bus voltage, the threshold where the drive's shunt switch activates)\n\n**Step 4 — Pick resistor power rating:** at least **1.5× P_avg** for continuous dissipation. If the resistor is wirewound, derate further for pulse current.\n\n**Step 5 — Verify against the drive's peak shunt current limit.** The drive's shunt MOSFET / IGBT has a current limit. If R is too low, peak current during clamping exceeds the drive's rating.\n\n**Typical values:**\n- 60 V drive, 2 kW peak regen: **R = 2–4 Ω, 200 W+ power rating**\n- 240 V drive, 5 kW peak regen: **R = 12–18 Ω, 500 W+ power rating**\n\nSee the drive's HW manual for exact terminal wiring and polarity.",
        source: "AMC_HWManual_FlexPro_PCB.pdf, AMC_HWManual_DigiFlex_Panel_CANopen.pdf"
      },
      a_regen_vertical: {
        answer: "**Vertical / overhauling loads = regen is constant whenever the load descends or overhauls.**\n\nGravity keeps the motor generating energy continuously while the load is falling, not just during decel.\n\n**Always-required for vertical loads:**\n1. **External shunt resistor** — the internal shunt was designed for intermittent decel events, not continuous gravity regen.\n2. **Mechanical brake** — when stopped, the drive can't hold a vertical load indefinitely on current alone (drive may fault, power may cycle). A fail-safe brake is almost always required by safety code for vertical axes. The brake engages when drive disables.\n3. **Worst-case power sizing** — calculate regen as: P = mass × g × v (falling power). For a 100 kg load falling at 0.5 m/s: P = 100 × 9.81 × 0.5 = 490 W continuous. Your shunt must handle this AVERAGE power all the way down.\n4. **Bus voltage margin** — if the motor has high back-EMF constant and the load drops fast, bus voltage can climb even with a shunt. Choose drive bus rating accordingly.\n5. **Controller logic** — during lowering, command velocity must match what gravity wants to do, else the drive fights gravity (motoring vs regen). Trajectory planning for descent should be gentle.",
        source: "AMC_AppNote_009.pdf, AMC_HWManual_FlexPro_PCB.pdf"
      },
    }
  },

  // =========================================================================
  // TREE 12: POWER / DC SUPPLY ISSUES
  // Sources: AN-058 (inrush current), AN-009 (over/under voltage), HW manuals.
  // =========================================================================
  power_supply: {
    title: "DC Supply / Power Problems",
    trigger_keywords: [
      "undervoltage", "under-voltage", "under voltage",
      "inrush", "inrush current", "soft start", "pre-charge",
      "brown out", "brownout", "bus voltage", "dc bus",
      "power supply", "ps sizing", "capacitor sizing",
      "blown fuse", "drive won't power up", "drive won't boot",
      "power on reset", "resets on startup",
    ],
    root: "q_power_symptom",
    nodes: {
      q_power_symptom: {
        question: "What's the power / supply symptom?",
        options: [
          { label: "Drive reports undervoltage fault", next: "q_uv_when" },
          { label: "Drive resets / brown-outs during motion", next: "a_pw_brownout" },
          { label: "Blown fuse or tripped breaker on power-up", next: "a_pw_inrush" },
          { label: "Drive won't power up at all (no LEDs)", next: "q_no_power_check" },
          { label: "Sizing a DC supply — how big does it need to be?", next: "a_pw_sizing" },
        ]
      },
      q_uv_when: {
        question: "When does the undervoltage fault happen?",
        options: [
          { label: "Constant, even before starting a motion", next: "a_uv_constant" },
          { label: "Only during acceleration", next: "a_uv_accel" },
          { label: "Only under heavy load", next: "a_uv_loaded" },
        ]
      },
      a_uv_constant: {
        answer: "**Constant undervoltage = DC supply is below the drive's minimum operating voltage.**\n\nEvery AMC drive has a minimum bus voltage in its datasheet (typically ~80% of nominal rating).\n\n**Fix:**\n1. **Measure DC voltage at the drive's power terminals** with a multimeter — not at the power supply. Cable voltage drop can sink you below the minimum even if the supply is nominal.\n2. **Check the drive's rated input range** on its datasheet. A 20–80 VDC drive won't tolerate 18 V.\n3. **If measured voltage is right but fault persists**, the drive's internal UV threshold sensor may be miscalibrated. Unlikely but possible — contact AMC tech support.\n4. Check you're powering **the right supply input** — AMC drives often have separate signal power and bus power terminals.",
        source: "AMC_AppNote_009.pdf, AMC_Datasheet_FE060-25-EM.pdf"
      },
      a_uv_accel: {
        answer: "**UV only on acceleration = supply can't deliver peak current; bus voltage sags.**\n\nDuring accel the drive pulls peak current. If the supply can't provide it instantaneously, bus voltage drops (V = V_open − I × R_source) below the UV threshold.\n\n**Fix:**\n1. **Upgrade to a bigger DC supply** — rated for your peak current, not just continuous. For servo applications, sizing for 2–3× continuous is common.\n2. **Add DC bus capacitance** — capacitors closer to the drive absorb peak demand, letting the supply catch up. Typical add: 4700–10000 µF at bus voltage.\n3. **Reduce acceleration rate** — less peak current demand.\n4. **Cable gauge** — long power cables add resistance. AWG-upsize the cable.\n5. **Connections** — loose screw terminals add resistance and heat. Check connections at both supply and drive ends.",
        source: "AMC_AppNote_009.pdf, AMC_AppNote_058.pdf"
      },
      a_uv_loaded: {
        answer: "**UV only under heavy load = same as accel-UV, just sustained.**\n\n**Fix:**\n1. **Supply undersized for your continuous current** — size the DC supply for the motor's actual continuous current × bus voltage, with 30% headroom.\n2. **Long cable drop** — at high continuous current, voltage drop across power cables adds up. V_drop = I × R_cable × 2 (round trip). Shorten or upsize cable.\n3. **Supply current-limiting kicking in** — some supplies fold back when approaching rated current. Check the supply datasheet.\n4. **Thermal protection on supply** — a hot supply may derate and drop output.\n5. If multiple drives share one supply, **each drive's peak current demand adds up**. Consider individual supplies for each axis.",
        source: "AMC_AppNote_009.pdf"
      },
      a_pw_brownout: {
        answer: "**Drive resets during motion = bus voltage dips below the POR (power-on-reset) threshold briefly.**\n\nShort dips can reset the drive without a clean UV fault because the drive's processor loses power faster than the UV detector can flag.\n\n**Fix:**\n1. **Add bus capacitance** — 4700–10 000 µF at the drive terminals. Large caps keep voltage up during brief demand spikes.\n2. **Upgrade supply's transient response** — switching supplies with fast loops, or linear supplies with huge capacitors, handle step loads better.\n3. **Kelvin-measure bus voltage at the drive during a demand spike** — use a scope to catch the dip. If bus drops below POR even briefly, you've found the cause.\n4. Inspect for **loose connections** — a high-resistance joint looks like a brown-out during current spikes.\n5. On **three-phase DC supplies**, check for balanced phases; imbalance causes ripple that can dip below POR.",
        source: "AMC_AppNote_009.pdf, AMC_AppNote_058.pdf"
      },
      a_pw_inrush: {
        answer: "**Blown fuse / tripped breaker on power-up = inrush current (per AN-058).**\n\nWhen a servo drive powers on, its DC bus capacitor bank is empty. For a few milliseconds, the capacitor looks like a short circuit to the supply, drawing **very high current** (hundreds of amps in large systems).\n\n**Per AN-058:** I_inrush ≈ V × C / t, where t is the risetime. A 60 V / 10 000 µF bus charging in 1 ms = 600 A inrush.\n\n**Fix:**\n1. **Add a soft-start / pre-charge circuit** — a resistor in series with the supply during power-up, bypassed by a contactor after 100–500 ms. Standard for industrial drives.\n2. **Use a slow-blow fuse** — rated for the continuous current but able to survive a brief inrush transient (I²t rating).\n3. **Use an inrush-limiting thermistor (NTC)** — its cold resistance limits initial current; warms up and drops to low resistance for normal operation. Requires minutes to reset after power-off.\n4. **Upsize the breaker / use time-delayed breaker** — a C-curve or D-curve magnetic trip tolerates inrush; a B-curve may not.\n5. **Multiple drives on one supply** — stagger power-up by 100+ ms each, or use a pre-charge circuit for the whole bus.",
        source: "AMC_AppNote_058.pdf"
      },
      q_no_power_check: {
        question: "The drive shows no LEDs at all — what have you verified?",
        options: [
          { label: "Haven't measured supply voltage yet", next: "a_np_measure_first" },
          { label: "Supply is correct at drive terminals", next: "a_np_internal" },
          { label: "Supply is dead too", next: "a_np_supply_dead" },
        ]
      },
      a_np_measure_first: {
        answer: "**Start with a multimeter at the drive's DC power terminals.**\n\n1. Put the multimeter on DC volts, black lead to drive's power ground, red to DC+.\n2. Measured voltage should be within the drive's rated input range (check datasheet).\n3. If the supply reads correct at the drive but no LEDs light up, the drive itself may be damaged — go to the 'supply correct' branch.\n4. If the supply reads zero or wrong voltage, go to the 'supply dead' branch.\n5. **Also verify signal / logic power separately** — some AMC drives have a separate 5 V or 24 V logic supply for the control section. The power LED may require logic power even when the bus is off.",
        source: "AMC_HWManual_FlexPro_PCB.pdf"
      },
      a_np_internal: {
        answer: "**Supply voltage correct but drive shows no life = internal drive fault.**\n\nAt this point the troubleshooting moves beyond the field — the drive's internal DC-DC regulator, processor, or protection circuitry has failed.\n\n**What to try before declaring it dead:**\n1. **Power cycle with all I/O disconnected** — isolate whether a fault on the I/O is preventing startup.\n2. **Check STO inputs** if equipped — some drives won't boot with STO disabled (inputs low) and no indication on the LEDs. Supply 24 V to both STO inputs and retry.\n3. **Check the reset input / enable** — not a boot requirement usually, but worth checking.\n4. **Inspect for obvious damage** — burn marks, cracked components, smell. Do not repower a visibly damaged drive.\n\nIf none of those help, the drive needs repair or replacement. Contact AMC tech support with the drive's serial number and the fault description.",
        source: "AMC_HWManual_FlexPro_PCB.pdf, AMC_Compliance_Safety_STO_FlexPro.pdf"
      },
      a_np_supply_dead: {
        answer: "**DC supply is not producing voltage.**\n\nThis is a supply-side problem, not a drive problem.\n\n1. **Verify AC input to the supply** (if it's a mains-powered DC supply).\n2. **Check supply's own fuses** — many DC supplies have internal fuses in addition to any external fuse.\n3. **Check supply's over-current / over-temp lockout** — some supplies latch off after a fault and need a power cycle or reset.\n4. **Check load** — disconnect the drive and see if the supply produces voltage with no load. If yes, something about the drive load is tripping the supply's over-current (likely inrush — see AN-058).\n5. **Swap supply** if available to isolate.",
        source: "AMC_AppNote_058.pdf"
      },
      a_pw_sizing: {
        answer: "**Sizing a DC supply for an AMC servo drive:**\n\n**Voltage:** pick the highest voltage your drive supports that fits your application. Higher bus = more top speed for a given motor Ke.\n\n**Continuous current:** total continuous current of the motor(s) under typical duty. This is NOT the drive's peak rating.\n    I_cont_total = Σ (motor_rated_current × duty_factor)\n\n**Peak current headroom:** peak demand during acceleration can be 2–3× continuous. The supply should either (a) be sized for peak, or (b) have local bus capacitance to buffer peaks.\n    Bus capacitance: 4700–10000 µF per drive as a starting point.\n\n**Regen headroom:** if the application regens, either the supply must absorb reverse current (rare) or you need an external shunt (common). See the regen tree.\n\n**Inrush protection:** required for any servo system. Pre-charge circuit or NTC thermistor. See AN-058.\n\n**Safety margin:** +30% on continuous current rating handles surges and component aging.\n\n**Example:** single FlexPro FE060-25-EM running at 15 A continuous, 30 A peak, 48 V bus:\n- Supply: 48 V, 20 A continuous (15 A + 30% margin)\n- Bus cap: 6800 µF / 63 V aluminum electrolytic + 100 nF ceramic for HF decoupling\n- External NTC or pre-charge resistor for inrush\n- No external shunt unless regen exceeds internal shunt rating",
        source: "AMC_AppNote_058.pdf, AMC_Datasheet_FE060-25-EM.pdf, AMC_HWManual_FlexPro_PCB.pdf"
      },
    }
  },

  // =========================================================================
  // TREE 13: MOTION PROFILES / WON'T REACH VELOCITY / STEP-DIR
  // Sources: AN-010 (profile time), AN-016 (velocity modes), AN-027 (motion
  // engine), AN-039 (step/direction), ACE manual.
  // =========================================================================
  motion_profile: {
    title: "Motion Profile / Won't Reach Commanded Velocity / Step-Dir",
    trigger_keywords: [
      "won't reach velocity", "doesn't reach speed", "motor too slow",
      "velocity limit", "speed limit",
      "pvt", "point to point", "trajectory", "motion engine",
      "step direction", "step/dir", "step and dir", "pulse direction",
      "stepper mode", "motion profile", "acceleration", "jerk",
      "s-curve", "scurve", "trapezoidal profile",
    ],
    root: "q_mp_symptom",
    nodes: {
      q_mp_symptom: {
        question: "What motion behavior are you troubleshooting?",
        options: [
          { label: "Motor won't reach commanded velocity", next: "q_wont_reach_why" },
          { label: "Acceleration feels wrong / different from commanded", next: "a_mp_accel_mismatch" },
          { label: "Step/direction input — pulses don't produce motion", next: "q_stepdir_problem" },
          { label: "PVT or trajectory mode — motion stutters between points", next: "a_mp_pvt" },
          { label: "Position moves complete early / late vs expected", next: "a_mp_timing" },
        ]
      },
      q_wont_reach_why: {
        question: "What's limiting the speed? Check the scope first — you'll see one of these:",
        options: [
          { label: "Current is saturated (pegged at limit)", next: "a_wr_current_sat" },
          { label: "Velocity flat-tops at a specific value below commanded", next: "a_wr_vel_limit" },
          { label: "Motor accelerates then falls back / oscillates", next: "a_wr_oscillates" },
          { label: "Smooth acceleration but stops short", next: "a_wr_short_time" },
        ]
      },
      a_wr_current_sat: {
        answer: "**Current saturated during motion = drive is giving everything it has, physics won't let it go faster.**\n\n**Root causes and fixes:**\n1. **Load inertia too high** for the accel rate you're asking. Reduce acceleration — scales linearly with required current.\n2. **Back-EMF eating bus voltage headroom.** At high speed, motor back-EMF approaches bus voltage. Remaining voltage drives current through the winding R + L impedance. If V_bus − V_bemf is too small, max current drops. **Fix:** raise bus voltage, or pick a motor with lower Ke (back-EMF constant).\n3. **Current limit set low.** Check the drive's peak and continuous current settings — may be misconfigured under the drive's rating.\n4. **Motor undersized** — if even the drive's peak current can't overcome load + friction + back-EMF, you need a bigger motor.\n5. **Trajectory demands more than the motor can produce** — acceleration × inertia = torque required, torque = Kt × current. Work backward from your motor's Kt and the drive's current limit to find the achievable acceleration.",
        source: "AMC_AppNote_015.pdf, AMC_AppNote_041.pdf"
      },
      a_wr_vel_limit: {
        answer: "**Velocity flat-tops below commanded = a velocity limit somewhere in the chain.**\n\n**Check (in order):**\n1. **Application velocity limit parameter** — many drives have a user-configurable max velocity that caps the command regardless of capability. Check and raise if needed.\n2. **Trajectory limit** — in CiA 402 profile mode, object 0x6080 (Max Motor Speed) or 0x607F (Max Profile Velocity) clips the command.\n3. **Feedforward / clamp** in ACE / DriveWare — some drives have a velocity feedforward with a clamp.\n4. **Physics limit (back-EMF)** — if the command is 100% achievable but the motor physically can't run that fast on your bus voltage, you'll see saturation (see the saturated-current branch).\n5. **Commutation failure at speed** — per AN-041, DigiFlex drives have a max sinusoidal commutation speed based on pole count and switching frequency. Above that, torque drops to zero. Lower speed or pick a higher-bandwidth drive.",
        source: "AMC_AppNote_016.pdf, AMC_AppNote_041.pdf"
      },
      a_wr_oscillates: {
        answer: "**Accelerates then falls back or oscillates = velocity loop unstable near the target.**\n\n**Fix:**\n1. **Reduce velocity Kp** — classic overshoot symptom. Go to the scope_tuning tree's velocity-loop branch.\n2. **Current loop may be bandwidth-limited** — if the velocity loop is asking for rapid current changes that the current loop can't deliver, you see secondary oscillation. Tune current loop first.\n3. **Commutation issue at the target speed** — if you're near the drive's max commutation speed (AN-041), transitions in commutation can cause a torque dip. You may see velocity droop right at a specific speed. Lower the target.\n4. **Low bus voltage** — if motor back-EMF at target speed equals bus voltage, the drive loses torque margin and the loop becomes marginal.",
        source: "AMC_AppNote_015.pdf, AMC_AppNote_041.pdf"
      },
      a_wr_short_time: {
        answer: "**Smooth acceleration but the move completes too short in time = profile parameters don't match physical capability.**\n\nYou asked for velocity V and accel A. Time-to-reach = V / A. If the drive can't produce A (current-limited), it accelerates slower and takes longer to reach V. The move finishes early from the drive's point of view (it ran out of profile points) even though from your stopwatch it took too long.\n\n**Per AN-010, profile time calculations:**\n- Trapezoidal move: t = V/A (accel) + distance_at_V (cruise) + V/A (decel)\n- If A is impossible, the profile runs at whatever A is achievable, and cruise time adjusts to make up the distance.\n\n**Fix:** verify the achievable acceleration given your current limits, then pick profile parameters that respect it. Or increase bus voltage / current limit to raise achievable A.",
        source: "AMC_AppNote_010.pdf"
      },
      a_mp_accel_mismatch: {
        answer: "**Actual acceleration differs from commanded:**\n\n1. **Unit mismatch** — most common. Drive may expect rev/s² while you're commanding counts/s², or vice versa. Check the drive's scaling parameters (CiA 402 SI-unit factor, or drive-specific scaling).\n2. **Current saturation** — covered in the won't-reach-velocity branch. When current saturates, actual accel < commanded.\n3. **Jerk (S-curve) enabled** — if jerk limiting is on, the drive rolls into peak accel gradually. Average accel over the ramp is less than peak. Disable S-curve or compare peak vs average.\n4. **Feedforward errors** — if velocity or acceleration feedforward is miscalibrated, the drive effectively runs slower than commanded.\n5. **Profile mode vs direct-command** — some profile modes internally re-calculate based on achievable limits; direct current commands don't. Verify you're in the expected mode.",
        source: "AMC_AppNote_010.pdf, AMC_AppNote_016.pdf"
      },
      a_mp_pvt: {
        answer: "**PVT (Position-Velocity-Time) stuttering = buffered points running out, or interpolation between points is wrong.**\n\n**Per AN-027 and the CANopen comm manual:**\n\n1. **Buffer underrun** — PVT needs continuous supply of new points. If the master falls behind, the drive runs out and holds position, causing stutter. Monitor the buffer-level object and feed points faster, or slow the trajectory.\n2. **Time units** — PVT points specify a time between them. If units don't match the drive's expectation, interpolation goes wrong. Check CiA 402 time-units configuration.\n3. **Large position deltas** — if two adjacent points imply a velocity jump beyond what the drive can deliver, you see a lag followed by a catch-up. Smooth out the trajectory.\n4. **Cycle time too fast** — if the master sends PVT points faster than the drive can consume them, overflow can manifest as stutter. Slow the send rate to the drive's expected cycle time.\n5. **Use the Motion Engine** (AN-027) instead — it generates the trajectory on the drive itself, avoiding host-side timing issues.",
        source: "AMC_AppNote_027.pdf, AMC_CommManual_CANopen.pdf"
      },
      a_mp_timing: {
        answer: "**Moves complete at the wrong time compared to expectations:**\n\n**Per AN-010:**\n\n1. **Total profile time = 2×(V/A) + (distance − V²/A) / V** for trapezoidal profiles — verify your math.\n2. **For short moves** that don't reach the commanded velocity, the profile becomes triangular: t = 2 × √(distance / A). The peak velocity is lower than commanded.\n3. **Jerk-limited (S-curve) profiles** add time. Total = trap time + jerk smoothing time. Exact amount depends on jerk value.\n4. **Pre-trigger latency** — if you're using PDO or host commands, there's a cycle-time latency between command and motion start. Typical: one PDO cycle (~1 ms) to start, several cycles for the drive to load the profile and begin.\n5. **Mechanical vs electrical** — if you're measuring with a stopwatch at the load, any gearbox / belt introduces compliance and settling time.",
        source: "AMC_AppNote_010.pdf"
      },

      // --- Step/Direction specific ---
      q_stepdir_problem: {
        question: "What's wrong with step/direction input?",
        options: [
          { label: "Motor doesn't move at all with pulses", next: "a_sd_no_motion" },
          { label: "Motor moves but wrong direction", next: "a_sd_wrong_dir" },
          { label: "Loses steps / position drifts", next: "a_sd_loses_steps" },
          { label: "How do I wire step/direction?", next: "a_sd_wiring" },
        ]
      },
      a_sd_wiring: {
        answer: "**Step/direction wiring (per AN-039 + HW manuals):**\n\n1. **Identify the drive's step/dir inputs** — usually labeled STEP, DIR, and common ground on the I/O connector. Check the HW manual for your specific drive.\n2. **Signal levels:**\n   - **5 V TTL** (most controllers) — wire directly if the drive supports 5 V logic.\n   - **24 V logic** (PLCs) — most AMC drives accept 24 V on these inputs; check the HW manual for input tolerance.\n3. **Differential vs single-ended:** AMC drives often accept both. Differential (STEP+/STEP−, DIR+/DIR−) is more noise-immune for long cable runs; single-ended is fine for short runs.\n4. **Pulse polarity:** rising-edge active is standard. Check the drive's step-polarity parameter if motion only happens on specific edges.\n5. **Step rate:** datasheet lists max input pulse frequency (typically 500 kHz to several MHz). Higher rates need better signal integrity.\n6. **Mode configuration:** configure the drive for **Stepper Emulation / Step-Direction mode** in software. Out of the box, an AMC servo is in another mode (velocity, current, PVT, etc.).",
        source: "AMC_AppNote_039.pdf, AMC_HWManual_DigiFlex_PCB_XEnv.pdf"
      },
      a_sd_no_motion: {
        answer: "**Pulses arriving but motor doesn't move.**\n\n**Fix:**\n1. **Confirm the drive is in step/direction mode** — in ACE / DriveWare, set the operating mode to Stepper Emulation or equivalent. If the drive is in velocity or current mode, step inputs are ignored.\n2. **Scope the STEP input** at the drive's terminal. You should see pulses at the expected frequency. If not, the controller's output or the wiring is broken.\n3. **Check polarity / edge configuration** — if the drive expects rising edge but the controller produces falling-edge pulses, no counts register.\n4. **Check steps-per-revolution scaling** — if configured at very high count ratio, a small pulse count may not produce visible motion.\n5. **Verify ENABLE state** — step mode still respects the drive's enable input. Drive disabled = no motion regardless of pulses.\n6. **Current limit / torque limit** — if the drive is loaded beyond its torque capacity, the motor slips and appears stationary even though the drive is trying. Reduce load or raise current limit.",
        source: "AMC_AppNote_039.pdf, AMC_SW_Manual_ACE.pdf"
      },
      a_sd_wrong_dir: {
        answer: "**Motor moves the wrong direction on step/direction command.**\n\n**Fix:**\n1. **Swap the polarity of the DIR signal** in the drive's configuration — most drives have a direction-invert parameter.\n2. **Or swap two motor phases** to physically reverse direction.\n3. **Or change the controller's DIR output polarity** if you have control over that.\n4. Don't swap multiple things at once — you'll just double-invert back to the wrong direction.",
        source: "AMC_AppNote_039.pdf"
      },
      a_sd_loses_steps: {
        answer: "**Step count and actual position diverge = steps being missed or faked.**\n\n**Fix:**\n1. **Noise on the STEP line** — long unshielded cables near motor power or AC lines pick up spurious pulses. Use differential signaling and shielded cable. See AN-023 on ferrite cores.\n2. **Step rate exceeds max input frequency** — check datasheet. If you're over the drive's spec, pulses are aliased or dropped.\n3. **Insufficient pulse width** — some drives need a minimum pulse width (e.g., 1 µs high + 1 µs low). Very fast controllers may produce pulses too narrow to count.\n4. **Motor stalling / missing steps** — under load above the drive's torque capability, the motor physically lags the commanded step count. Servo drives usually report this as position-error fault; if not, increase current or reduce load.\n5. **Encoder scaling mismatch** — if you're comparing commanded steps to encoder counts, make sure the ratio matches your configured scaling.",
        source: "AMC_AppNote_039.pdf, AMC_AppNote_023.pdf"
      },
    }
  },
};
