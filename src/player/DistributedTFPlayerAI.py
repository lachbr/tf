
from tf.character.DistributedCharAI import DistributedCharAI
from direct.distributed2.ServerConfig import *
from .DistributedTFPlayerShared import DistributedTFPlayerShared

from direct.directnotify.DirectNotifyGlobal import directNotify

from .PlayerCommand import PlayerCommand
from .InputButtons import InputFlag
from .TFPlayerAnimStateAI import TFPlayerAnimStateAI
from .PlayerAnimEvent import PlayerAnimEvent
from .TFClass import *
from .DViewModelAI import DViewModelAI
from .ObserverMode import ObserverMode
from tf.weapon.TakeDamageInfo import addMultiDamage

from tf.tfbase import TFGlobals
from tf.tfbase.TFGlobals import Contents, CollisionGroup, TakeDamage, DamageType

from panda3d.core import *
from panda3d.pphysics import PhysRayCastResult, PhysQueryNodeFilter

import copy
import random

tf_damage_range = 0.5
tf_damageforcescale_other = 3.0

TF_DEATH_ANIMATION_TIME = 2.0
spec_freeze_time = ConfigVariableDouble("spec-freeze-time", 4.0)
spec_freeze_traveltime = ConfigVariableDouble("spec-freeze-travel-time", 0.4)

class CommandContext:
    def __init__(self):
        self.backupCmds = []
        self.newCmds = []
        self.cmds = []
        self.totalCmds = 0
        self.droppedPackets = 0
        self.paused = False

class PlayerCmdInfo:
    def __init__(self):
        self.time = 0.0
        self.numCmds = 0
        self.droppedPackets = 0

class DistributedTFPlayerAI(DistributedCharAI, DistributedTFPlayerShared):
    notify = directNotify.newCategory("DistributedTFPlayerAI")
    notify.setDebug(True)

    MaxCMDBackup = 64

    def __init__(self):
        DistributedCharAI.__init__(self)
        DistributedTFPlayerShared.__init__(self)
        self.animState = TFPlayerAnimStateAI(self)
        self.commandContexts = []
        self.lastMovementTick = -1
        self.simulationTick = 0
        self.paused = False
        self.lastCmd = PlayerCommand()
        self.currentCommand = None
        self.lastRunCommandNumber = 0
        self.viewAngles = Vec3(0, 0, 0)
        self.nextAttack = 0.0
        self.forceJoint = -1
        self.bulletForce = Vec3(0)

        # Also give them a view model
        self.viewModel = DViewModelAI()

    def getWorldSpaceCenter(self):
        return self.getPos(NodePath()) + (0, 0, self.classInfo.ViewHeight / 2)

    def getClassSize(self):
        mins = self.classInfo.BBox[0]
        maxs = self.classInfo.BBox[1]
        return Vec3(maxs[0] - mins[0], maxs[1] - mins[1], maxs[2] - mins[2])

    def onTakeDamage_alive(self, info):
        vecDir = Vec3(0)
        if info.inflictor:
            vecDir = info.inflictor.getWorldSpaceCenter() - Vec3(0, 0, 10) - self.getWorldSpaceCenter()
            vecDir.normalize()

        force = vecDir * -self.damageForce(self.getClassSize(), info.damage, tf_damageforcescale_other)
        self.velocity += force

        self.health -= int(info.damage + 0.5)
        self.health = max(0, self.health)
        if self.health <= 0:
            # Died.
            self.die(info.attacker, info)

    def damageForce(self, size, damage, scale):
        force = damage * ((48 * 48 * 82.0) / (size[0] * size[1] * size[2])) * scale
        if force > 1000:
            force = 1000
        return force

    def onTakeDamage(self, inputInfo):
        info = inputInfo#copy.deepcopy(inputInfo)

        if not info.damage:
            return

        if self.isDead:
            return

        healthBefore = self.health
        if not base.game.playerCanTakeDamage(self, info.attacker):
            return

        # Save damage force for ragdolls.
        self.bulletForce = Vec3(info.damageForce)
        #self.bulletForce[0] = max(-15000, min(15000, self.bulletForce[0]))
        #self.bulletForce[1] = max(-15000, min(15000, self.bulletForce[1]))
        #self.bulletForce[2] = max(-15000, min(15000, self.bulletForce[2]))

        # If we're not damaging ourselves, apply randomness
        if info.attacker != self and not (info.damageType & (DamageType.Drown | DamageType.Fall)):
            damage = 0
            randomDamage = info.damage * tf_damage_range#.getValue()
            minFactor = 0.25
            maxFactor = 0.75
            if info.damageType & DamageType.UseDistanceMod:
                distance = max(1.0, (self.getWorldSpaceCenter() - info.attacker.getWorldSpaceCenter()).length())
                optimalDistance = 512.0

                center = TFGlobals.remapValClamped(distance / optimalDistance, 0.0, 2.0, 1.0, 0.0)
                if info.damageType & DamageType.NoCloseDistanceMod:
                    if center > 0.5:
                        # Reduce the damage bonus at close rangae
                        center = TFGlobals.remapVal(center, 0.5, 1.0, 0.5, 0.65)
                minFactor = max(0.0, center - 0.25)
                maxFactor = min(1.0, center + 0.25)

            randomVal = random.uniform(minFactor, maxFactor)

            #if (randomVal > 0.5):

            out = TFGlobals.simpleSplineRemapValClamped(randomVal, 0, 1, -randomDamage, randomDamage)
            damage = info.damage + out
            info.damage = damage

        self.onTakeDamage_alive(info)

        if self.health > 0:
            # If still alive, flinch
            self.doAnimationEvent(PlayerAnimEvent.Flinch)

        self.sendUpdate('pain')

    def traceAttack(self, info, dir, hit):
        if self.takeDamageMode != TakeDamage.Yes:
            return

        actor = hit.getActor()
        data = actor.getPythonTag("hitbox")
        if data:
            # Save this joint for the ragdoll.
            self.forceJoint = data[1].joint
        else:
            self.forceJoint = -1

        attacker = info.attacker
        if attacker:
            # Prevent team damage so blood doesn't appear.
            if not base.game.playerCanTakeDamage(self, attacker):
                return

        addMultiDamage(info, self)

    def doAnimationEvent(self, event, data = 0):
        self.animState.doAnimationEvent(event, data)

    def die(self, killer, info):
        self.isDead = True
        # Become a ragdoll.
        self.sendUpdate('becomeRagdoll', [self.forceJoint, info.damagePosition, info.damageForce])
        # Respawn after 5 seconds.
        self.addTask(self.respawnTask, 'respawn', appendTask = True)
        self.observerTarget = killer.doId
        self.observerMode = ObserverMode.DeathCam
        self.deathTime = globalClock.getFrameTime()
        self.playedFreezeSound = False
        self.abortFreezeCam = False

    def respawnTask(self, task):

        now = globalClock.getFrameTime()

        timeInFreeze = spec_freeze_traveltime.getValue() + spec_freeze_time.getValue()
        freezeEnd = (self.deathTime + TF_DEATH_ANIMATION_TIME + timeInFreeze)
        if not self.playedFreezeSound and self.observerTarget != self.doId:
            # Start the sound so that it ends at the freezecam lock on time
            freezeSoundLength = 0.3
            freezeSoundTime = (self.deathTime + TF_DEATH_ANIMATION_TIME) + spec_freeze_traveltime.getValue() - freezeSoundLength
            if now >= freezeSoundTime:
                base.net.game.d_emitSound("TFPlayer.FreezeCam", Point3(), self.owner)
                self.playedFreezeSound = True

        if now >= (self.deathTime + TF_DEATH_ANIMATION_TIME): # allow x seconds death animation/death cam
            if self.observerTarget != self.doId:
                if not self.abortFreezeCam and now < freezeEnd:
                    # Start zooming in on the killer and do the freeze cam.
                    self.observerMode = ObserverMode.FreezeCam
                    return task.cont

        if now < freezeEnd:
            return task.cont

        self.observerTarget = 0
        self.observerMode = ObserverMode.Off

        # Respawn now.

        # Refill health
        self.health = self.maxHealth
        # Refill ammo
        for wpnId in self.weapons:
            wpn = base.net.doId2do.get(wpnId)
            wpn.ammo = wpn.maxAmmo
            wpn.clip = wpn.maxClip
        # Set to the primary weapon
        self.setActiveWeapon(0)
        self.setPos(Point3(random.uniform(-128, 128), random.uniform(-128, 128), 0))
        self.setHpr(Vec3(random.uniform(-180, 180), 0, 0))
        self.isDead = False
        self.sendUpdate('respawn')
        return task.done

    def changeClass(self, cls):
        self.stripWeapons()
        self.tfClass = cls
        self.classInfo = ClassInfos[self.tfClass]
        self.maxHealth = self.classInfo.MaxHealth
        self.health = self.maxHealth
        self.setModel(self.classInfo.PlayerModel)
        self.animState.initGestureSlots()

    def stripWeapons(self):
        for wpnId in self.weapons:
            wpn = base.sv.doId2do.get(wpnId)
            if not wpn:
                continue
            base.sv.deleteObject(wpn)
        self.weapons = []
        self.activeWeapon = -1

    def setActiveWeapon(self, index):
        if self.activeWeapon == index:
            # Already the active weapon.
            return

        if self.activeWeapon > 0 and self.activeWeapon < len(self.weapons):
            # Deactive the old weapon.
            wpnId = self.weapons[self.activeWeapon]
            wpn = base.sv.doId2do.get(wpnId)
            if wpn:
                wpn.deactivate()

        self.activeWeapon = index
        if self.activeWeapon < 0 or self.activeWeapon >= len(self.weapons):
            return

        # Activate the new weapon.
        wpnId = self.weapons[self.activeWeapon]
        wpn = base.sv.doId2do.get(wpnId)
        if wpn:
            wpn.activate()

    def giveWeapon(self, wpnId, makeActive = True):
        if wpnId in self.weapons:
            return

        wpn = base.sv.doId2do.get(wpnId)
        if wpn:
            wpn.setPlayerId(self.doId)

        self.weapons.append(wpnId)
        if makeActive or len(self.weapons) == 1:
            # Make it active if specifically requested or this is the only
            # weapon.
            self.setActiveWeapon(self.weapons.index(wpnId))

    def getActiveWeapon(self):
        return self.activeWeapon

    def getAbsVelocity(self):
        return self.vel

    def getVelocity(self):
        """
        Returns local-space velocity.
        """
        quat = self.getQuat(NodePath())
        quat.invertInPlace()
        return quat.xform(self.vel)

    def announceGenerate(self):
        DistributedCharAI.announceGenerate(self)
        DistributedTFPlayerShared.announceGenerate(self)
        self.reparentTo(base.render)

    def generate(self):
        # Generate our view model as well.
        self.viewModel.setPlayerId(self.doId)
        self.viewModel.team = self.team
        self.viewModel.skin = self.skin
        base.sv.generateObject(self.viewModel, self.zoneId)

    def delete(self):
        # Get rid of the view model along with us.
        base.sv.deleteObject(self.viewModel)

        DistributedCharAI.delete(self)
        DistributedTFPlayerShared.disable(self)

    def getCommandContext(self, i):
        assert i >= 0 and i < len(self.commandContexts)

        return self.commandContexts[i]

    def allocCommandContext(self):
        self.commandContexts.append(CommandContext())
        if len(self.commandContexts) > 1000:
            self.notify.error("Too many command contexts")
        return self.commandContexts[len(self.commandContexts) - 1]

    def removeCommandContext(self, i):
        self.commandContexts.remove(self.commandContexts[i])

    def removeAllCommandContexts(self):
        self.commandContexts = []

    def removeAllCommandContextsExceptNewest(self):
        count = len(self.commandContexts)
        toRemove = count - 1
        if toRemove > 0:
            del self.commandContexts[0:toRemove]

        if len(self.commandContexts) == 0:
            # This shouldn't happen.
            assert False
            self.allocCommandContext()

        return self.commandContexts[0]

    def replaceContextCommands(self, ctx, commands, count):
        ctx.totalCmds = count
        ctx.droppedPackets = 0
        ctx.newCmds = []
        for i in range(count):
            ctx.newCmds.append(copy.deepcopy(commands[i]))
        ctx.cmds = ctx.newCmds
        ctx.backupCmds = []

    def determineSimulationTicks(self):
        ctxCount = len(self.commandContexts)

        simulationTicks = 0

        # Determine how much time we will be running this frmae and fixup
        # player clock as needed.
        for i in range(ctxCount):
            ctx = self.getCommandContext(i)
            assert ctx
            assert len(ctx.newCmds) > 0
            assert ctx.droppedPackets >= 0

            # Determine how long it will take to run those packets.
            simulationTicks += len(ctx.newCmds) + ctx.droppedPackets

        return simulationTicks

    def adjustPlayerTimeBase(self, simulationTicks):
        assert simulationTicks >= 0
        if simulationTicks < 0:
            return

        if base.sv.getMaxClients() == 1:
            # Set tickbase so that player simulation tick matches
            # base.sv.tickCount after all commands have been executed.
            self.tickBase = base.tickCount - simulationTicks + base.currentTicksThisFrame
        else:
            correctionSeconds = max(0.0, min(1.0, sv_clockcorrection_msecs.getValue() / 100.0))
            correctionTicks = base.timeToTicks(correctionSeconds)

            # Set the target tick correctionSeconds (rounded to ticks) ahead in
            # the future.  This way the client can alternate around this target
            # tick without getting smaller than base.sv.tickCount.  After
            # running the commands, simulation time should be equal or after
            # current base.sv.tickCount, otherwise the simulation time drops
            # out of the client side interpolated var history window.

            idealFinalTick = base.tickCount + correctionTicks
            estimatedFinalTick = self.tickBase + simulationTicks

            # If client gets ahead of this, we'll need to correct.
            tooFastLimit = idealFinalTick + correctionTicks
            # If the client gets behind this, we'll also need to correct.
            tooSlowLimit = idealFinalTick - correctionTicks

            # See if we are too fast.
            if estimatedFinalTick > tooFastLimit or estimatedFinalTick < tooSlowLimit:
                correctedTick = idealFinalTick - simulationTicks + base.currentTicksThisFrame
                self.tickBase = correctedTick

    def processPlayerCommands(self, backupCmds, newCmds, totalCommands, paused):
        ctx = self.allocCommandContext()
        assert ctx

        ctx.backupCmds = backupCmds
        ctx.newCmds = newCmds
        ctx.cmds = backupCmds + newCmds
        ctx.totalCmds = totalCommands
        ctx.paused = paused

        if ctx.paused:
            for cmd in ctx.cmds:
                cmd.buttons = InputFlag.Empty
                cmd.move = Vec3(0)
                cmd.viewAngles = self.viewAngles

            ctx.droppedPackets = 0

        self.paused = paused

        if paused:
            self.forceSimulation()
            self.simulate()

    def simulate(self):
        self.bulletForce = Vec3(0)

        DistributedCharAI.simulate(self)

        # Make sure to not simulate this guy twice per frame.
        if self.simulationTick == base.tickCount:
            return

        self.simulationTick = base.tickCount

        # See how many PlayerCommands are queued up for running.
        simulationTicks = self.determineSimulationTicks()

        # If some time will elapse, make sure our clock (self.tickBase) starts
        # at the correct time.
        if simulationTicks > 0:
            self.adjustPlayerTimeBase(simulationTicks)

        # Store off true server timestamps
        saveFrameTime = globalClock.getFrameTime()
        saveDt = globalClock.getDt()

        commandContextCount = len(self.commandContexts)

        # Build a list of available commands.
        availableCommands = []

        # Contexts go from oldest to newest
        for i in range(commandContextCount):
            # Get oldest (newer are added to tail)
            ctx = self.getCommandContext(i)

            if len(ctx.cmds) == 0:
                continue

            numBackup = len(ctx.backupCmds)

            # If we haven't dropped too many packets, then run some commands
            if ctx.droppedPackets < 24:
                droppedCmds = ctx.droppedPackets

                # Run the last known cmd for each dropped cmd we don't have a
                # backup for.
                while droppedCmds > numBackup:
                    self.lastCmd.tickCount += 1
                    availableCommands.append(copy.deepcopy(self.lastCmd))
                    droppedCmds -= 1

                # Now run the "history" commands if we still have dropped packets.
                while droppedCmds > 0:
                    cmdNum = numBackup - droppedCmds
                    availableCommands.append(copy.deepcopy(ctx.backupCmds[cmdNum]))
                    droppedCmds -= 1

            # Now run any new commands.  Most recent command is at the tail.
            for i in range(len(ctx.newCmds)):
                availableCommands.append(copy.deepcopy(ctx.newCmds[i]))

            # Save off the last good command in case we drop > numBackup
            # packets and need to rerun them.  We'll use this to "guess" at
            # what was in the missing packets.
            self.lastCmd = copy.deepcopy(ctx.cmds[len(ctx.cmds) - 1])

        # base.currentTicksThisFrame == number of ticks remaining to be run, so
        # we should take the last N PlayerCommands and postpone them until the
        # next frame.

        # If we're running multiple ticks this frame, don't peel off all of the
        # commands, spread them out over the server ticks.  Use blocks of two
        # in alternate ticks.

        # False would be sv_alternateticks, if I want to implement that.
        cmdLimit = 2 if False else 1
        cmdsToRun = len(availableCommands)
        if base.currentTicksThisFrame >= cmdLimit and len(availableCommands) > cmdLimit:
            cmdsToRollOver = min(len(availableCommands), base.currentTicksThisFrame - 1)#

            cmdsToRun = len(availableCommands) - cmdsToRollOver
            assert cmdsToRun >= 0

            # Clear all contexts except last one.
            if cmdsToRollOver > 0:
                ctx = self.removeAllCommandContextsExceptNewest()
                self.replaceContextCommands(ctx, availableCommands, cmdsToRollOver)
            else:
                # Clear all contexts
                self.removeAllCommandContexts()
        else:
            # Clear all contexts.
            self.removeAllCommandContexts()

        # Now run the commands.
        if cmdsToRun > 0:
            for i in range(cmdsToRun):
                self.runPlayerCommand(availableCommands[i], base.deltaTime)

        # Restore the true server clock.
        base.setFrameTime(saveFrameTime)
        base.setDeltaTime(saveDt)

    def runPlayerCommand(self, cmd, deltaTime):
        if self.isDead:
            return

        self.currentCommand = cmd

        base.setFrameTime(self.tickBase * base.intervalPerTick)
        base.setDeltaTime(base.intervalPerTick)

        base.net.predictionRandomSeed = cmd.randomSeed

        # Do weapon selection.
        if cmd.weaponSelect >= 0 and cmd.weaponSelect < len(self.weapons) and cmd.weaponSelect != self.activeWeapon:
            self.setActiveWeapon(cmd.weaponSelect)

        self.updateButtonsState(cmd.buttons)

        self.viewAngles = cmd.viewAngles

        # Get the active weapon
        wpn = None
        if self.activeWeapon != -1:
            wpnId = self.weapons[self.activeWeapon]
            wpn = base.sv.doId2do.get(wpnId)

        if wpn:
            wpn.itemPreFrame()

        # Run the movement.
        DistributedTFPlayerShared.runPlayerCommand(self, cmd, deltaTime)

        self.notify.debug("Running command %s" % str(cmd))

        if wpn:
            wpn.itemPostFrame()

        self.animState.update()

        # Let time pass.
        self.tickBase += 1

        # Store off the command number of this command so we can inform the
        # client that we ran it.
        self.lastRunCommandNumber = max(self.lastRunCommandNumber, cmd.commandNumber)

        #print("Server ran command", cmd.commandNumber, "at pos", self.getPos())

        base.net.predictionRandomSeed = 0
        self.currentCommand = None

    def playerCommand(self, data):
        """ Player command sent to us by the client. """

        client = base.sv.clientSender

        dg = Datagram(data)
        dgi = DatagramIterator(dg)

        if self.lastMovementTick == base.tickCount:
            self.notify.debug("Received more than one command this tick")
            return

        backupCommands = dgi.getUint8()
        newCommands = dgi.getUint8()
        totalCommands = newCommands + backupCommands

        self.notify.debug("Got %i new cmds and %i backup cmds" % (newCommands, backupCommands))

        backupCmds = []
        newCmds = []

        assert newCommands >= 0
        assert (totalCommands - newCommands) >= 0

        if totalCommands < 0 or totalCommands >= (self.MaxCMDBackup - 1):
            self.notify.warning("Too many cmds (%i) sent to us from client %i" % (totalCommands, client.id))
            return

        nullCmd = PlayerCommand()
        prev = nullCmd
        # Backups come first
        for i in range(backupCommands):
            self.notify.debug("Reading backup cmd %i" % i)
            to = PlayerCommand.readDatagram(dgi, prev)
            backupCmds.append(to)
            prev = to
        # Now the new commands
        for i in range(newCommands):
            self.notify.debug("Reading new cmd %i" % i)
            to = PlayerCommand.readDatagram(dgi, prev)
            newCmds.append(to)
            prev = to

        self.processPlayerCommands(backupCmds, newCmds, totalCommands, False)
